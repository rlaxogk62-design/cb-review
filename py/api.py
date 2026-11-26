"""대시보드 API: 화면이 부르는 /api/… 요청 하나를 처리한다.

사내 서버(server.py)와 외부 사이트(브라우저 안 Pyodide)가 같은 route()를 쓴다.
ras(법인 서버 모듈)는 사내 버전에만 있다 — 없으면 주가는 야후, 나이스 등급·금리 곡선은 직접 입력.
"""
import datetime as dt
import json
import re
from urllib.parse import parse_qs, quote

import dart
import engine
import export
import market

try:
    import ras
except ImportError:
    ras = None

NO_RAS = "나이스 등급·금리 곡선 자동 조회는 사내 버전에서만 됩니다. 곡선 등급코드를 고르고 Rf·Rd 곡선을 붙여넣어 주세요."


def load_issue(corp_code, rcept_no, val_date=None):
    """회차 하나를 불러온다: 발행조건 파싱 → 평가기준일까지의 조정·잔액 이력 반영 → 재무·감사 → 유의 포인트."""
    issues = dart.cb_list(corp_code)
    it = next((x for x in issues if x["rcept_no"] == rcept_no), None)
    if not it:
        raise ValueError("해당 공시를 찾지 못했습니다.")
    fields = dart.parse_cb(it["raw"], it["kind"])
    items = dart.disclosures(corp_code)
    warnings = []
    try:
        hist = dart.history(corp_code, it["bd_tm"], it["kind"], items, val_date)
    except Exception as ex:
        hist, _ = [], warnings.append(f"조정·잔액 이력을 읽지 못했습니다: {ex}")
    dart.apply_history(fields, hist, val_date)
    try:
        acts = dart.corporate_actions(corp_code, items)
    except Exception as ex:
        acts, _ = [], warnings.append(f"주식병합·분할 공시를 읽지 못했습니다: {ex}")
    applied = dart.adjust_for_actions(fields, acts, dart.kdate(it.get("bddd")), val_date, hist)
    try:
        fa = dart.fin_audit(corp_code, val_date)
    except Exception as ex:
        fa, _ = {}, warnings.append(f"재무·감사 정보를 읽지 못했습니다: {ex}")
    al, summary = dart.alerts(corp_code, fields, items, val_date, hist, fa, it["kind"])
    vd = val_date or dt.date.today().isoformat()
    for a in acts:
        when = a["listing"] or a["effective"] or a["board"]
        txt = f"{a['kind']} {dart._fmt_ratio(a['factor'])} · 효력 {a['effective'] or '-'} · 상장 {a['listing'] or '-'}"
        if a in applied:
            al.insert(0, {"level": "warn", "cat": "회차", "title": f"{txt} — 전환가·하한에 반영",
                          "why": f"발행결정 공시 값에 {a['factor']:g}배를 곱했습니다 (전환가 조정 조항: 병합·분할 비율만큼 조정). 최초 전환가·하한·현 전환가를 확인하세요.",
                          "items": [{"rcept_no": a["rcept_no"], "date": a["board"], "report_nm": a["report_nm"]}]})
        elif when and when > vd:
            al.insert(0, {"level": "warn", "cat": "회차", "title": f"{txt} — 평가기준일 이후",
                          "why": f"야후·RAS 주가는 이 사건으로 소급조정돼 있어 평가기준일 주가를 {a['factor']:g}로 나눠 당시 실제 주가로 바꿉니다 (Temp 메모: KRX 수정전 주가). 변동성은 영향 없습니다.",
                          "items": [{"rcept_no": a["rcept_no"], "date": a["board"], "report_nm": a["report_nm"]}]})
    for f in fields.values():
        evs = [e for e in (f.get("evidence") or []) if e and e.get("rcept_no")]
        found = False
        for e in evs:
            ok, txt = dart.locate_evidence(e)
            e["found"] = ok
            if ok:
                e["text"] = txt
                found = True
        if f.get("conf") == "high" and not found:
            f["conf"] = "mid"
            why = ("공시 API 값이지만 원문에서 같은 문구 위치를 찾지 못했습니다. 원문을 확인하세요." if evs
                   else "원문 근거 문구 없이 규칙·기본값으로 정한 값입니다. 원문을 확인하세요.")
            f["note"] = (f["note"] + " / " + why) if f.get("note") else why
    return {"issue": {k: v for k, v in it.items() if k != "raw"}, "fields": fields, "alerts": al, "summary": summary,
            "history": hist, "warnings": warnings, "actions": acts,
            "disclosures": [{"rcept_no": d["rcept_no"], "date": d["rcept_dt"], "report_nm": d["report_nm"].strip()} for d in items[:300]]}


def engine_params(st):
    f = {k: (v or {}).get("value") for k, v in st["fields"].items()}
    m = st["meta"]
    need = {"발행일": f.get("발행일"), "만기일": f.get("만기일"), "잔여총액": f.get("잔여총액"),
            "현조정행사가격": f.get("현조정행사가격"), "평가기준일": m.get("val_date"),
            "주가": st["market"].get("S"), "변동성": st["market"].get("sigma")}
    miss = [k for k, v in need.items() if v in (None, "", 0)]
    if miss:
        raise ValueError("계산에 필요한 값이 비어 있습니다: " + ", ".join(miss))
    if len([x for x in st["curves"]["rf"] if x not in (None, "")]) < 2 or len([x for x in st["curves"]["rd"] if x not in (None, "")]) < 2:
        raise ValueError("Rf·Rd 금리 곡선을 붙여넣어 주세요 (Temp DW의 DV~EK, EM~EZ 열).")
    call_on = bool(f.get("Call초일") or f.get("Call일정"))
    return dict(
        val_date=m["val_date"], issue_date=f["발행일"], maturity=f["만기일"], face=f["잔여총액"],
        coupon=f.get("표면이자율") or 0, coupon_freq=f.get("연간이자지급횟수") or 1,
        ytm=f.get("만기이자율") or 0, ytm_comp=f.get("상환활증방식") or "연복리", redem_ratio=f.get("RedemR"),
        conv_price=f["현조정행사가격"], conv_start=f.get("전환초일") or f["발행일"], conv_end=f.get("전환말일"),
        put_start=f.get("Put초일"), put_end=f.get("Put말일"), put_freq=f.get("Put연회수") or 12,
        put_schedule=f.get("Put일정") if m.get("use_tables", True) else None,
        ytp=f.get("YTP") or 0, ytp_comp=f.get("YTP복리") or "연복리",
        call_start=f.get("Call초일"), call_end=f.get("Call말일"), call_freq=f.get("Call연회수") or 12,
        call_schedule=f.get("Call일정") if m.get("use_tables", True) else None,
        ytc=f.get("YTC") or 0, ytc_comp=f.get("YTC복리") or "연복리", call_rank=f.get("Call순위") or "선순위",
        call_ratio=(f.get("Call한도") if f.get("Call한도") is not None else 1.0) if call_on else 0,
        first_conv_price=f.get("최초전환가격") or f["현조정행사가격"],
        refix_type=f.get("상하향조정") or "일반", refix_floor=f.get("Refix최소"),
        refix_freq=f.get("전환가조정연회수") or 12, refix_start=f.get("전환가격조정시작일"),
        S=st["market"]["S"], sigma=st["market"]["sigma"], steps_per_year=m.get("steps_per_year") or 52,
        rf_curve=st["curves"]["rf"], rd_curve=st["curves"]["rd"],
    )


JSON = "application/json; charset=utf-8"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def route(method, path, query="", body=None):
    """API 한 건 처리 → (상태코드, 내용, 콘텐츠타입, 추가헤더)."""
    q = {k: v[0] for k, v in parse_qs(query or "").items()}
    if method == "GET":
        if path == "/api/search":
            return 200, dart.search(q.get("q")), JSON, None
        if path == "/api/issues":
            return 200, [{k: v for k, v in x.items() if k != "raw"} for x in dart.cb_list(q["corp_code"])], JSON, None
        if path == "/api/load":
            return 200, load_issue(q["corp_code"], q["rcept_no"], q.get("val_date")), JSON, None
        if path == "/api/doc":
            if not re.fullmatch(r"\d{14}", q.get("rcept_no", "")):
                raise ValueError("접수번호 형식 오류")
            return 200, dart.document_html(q["rcept_no"]), "text/html; charset=utf-8", None
        if path == "/api/market":
            r = market.stock(q["stock_code"], q["val_date"], q.get("market"), q.get("period") or "1년",
                             q.get("maturity") or None, int(q.get("window", 180)), ras=ras)
            if q.get("corp_code"):
                acts = dart.corporate_actions(q["corp_code"])
                div, after = dart.price_divisor_after(acts, r["price_date"])
                if abs(div - 1) > 1e-9:
                    r["adjusted_price"] = r["price"]
                    r["price"] = round(r["price"] / div, 4)
                    r["price_note"] = (f"평가기준일 이후 {', '.join(a['kind'] + ' ' + dart._fmt_ratio(a['factor']) + ' (' + (a['listing'] or a['effective'] or '') + ')' for a in after)}"
                                       f" → 소급조정 주가 {r['adjusted_price']:,.0f}원 ÷ {div:g} = {r['price']:,.0f}원 (당시 실제 주가)")
            return 200, r, JSON, None
        if path in ("/api/credit", "/api/curves") and ras is None:
            raise RuntimeError(NO_RAS)
        if path == "/api/credit":
            co = dart.company(q["corp_code"])
            city = market.city_from_address(co.get("adres"))
            n = ras.nice_credit(q.get("company") or co["corp_name"], city)
            g = market.grade_asof(n["rows"], q["val_date"])
            private = q.get("private", "1") == "1"
            return 200, {**n, "city": city, "asof": g, "code": market.curve_code(g and g["신용등급"], private)}, JSON, None
        if path == "/api/curves":
            return 200, ras.curves(q["val_date"], q.get("grade") or None), JSON, None
        if path == "/api/latest":
            return 200, dart.latest(q["corp_code"], int(q.get("days", 45))), JSON, None
    if method == "POST":
        st = json.loads(body or "{}")
        if path == "/api/value":
            out = engine.value(engine_params(st))
            if st["meta"].get("issue_basis"):
                out["result"] = engine.implied_conv_at_issue(out["result"], float(st["fields"]["잔여총액"]["value"]))
            return 200, out, JSON, None
        if path in ("/api/export/review", "/api/export/dw"):
            m = st["meta"]
            ymd = dt.date.today().strftime("%y%m%d")
            if path.endswith("review"):
                data, name = export.review_xlsx(st), f"{m['company']}_FV_review_RAS_{ymd}.xlsx"
            else:
                data, name = export.dw_xlsx(st), f"Temp_DW입력_{m['company']}_{m.get('bd_tm') or ''}{m.get('kind') or 'CB'}_{ymd}.xlsx"
            return 200, data, XLSX, {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"}
    return 404, {"error": "not found"}, JSON, None
