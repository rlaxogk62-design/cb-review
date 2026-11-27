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
import notes
import screen

try:
    import ras
except ImportError:
    ras = None

NO_RAS = "나이스 등급·금리 곡선 자동 조회는 사내 버전에서만 됩니다. 곡선 등급코드를 고르고 Rf·Rd 곡선을 붙여넣어 주세요."


def issues_for(corp_code, val_date=None):
    """회차 카드: CB·BW·EB 발행결정 + 정기보고서 주석의 RCPS·CPS·SO (비상장 포함)."""
    out = [{k: v for k, v in x.items() if k != "raw"} for x in dart.cb_list(corp_code)]
    try:
        out += notes.note_instruments(corp_code, val_date)["items"]
    except Exception:
        pass
    return out


def _check_evidence(fields):
    """녹색(공시에서 확인)은 원문에서 근거 문구를 실제로 찾았을 때만.
    - 근거 문구는 원문 표기로 바꾼다 (API '2025년 08월 11일' → 원문 '2025.08.11') → 형광펜이 찾는다
    - 근거가 없거나 원문에서 못 찾으면 주황(추정)으로 낮추고 이유를 적는다"""
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
            why = ("공시 값이지만 원문에서 같은 문구 위치를 찾지 못했습니다. 원문을 확인하세요." if evs
                   else "원문 근거 문구 없이 규칙·기본값으로 정한 값입니다. 원문을 확인하세요.")
            f["note"] = (f["note"] + " / " + why) if f.get("note") else why


def _note_alerts(kind, f, raw, fa, val_date, rep):
    """RCPS·SO 전용 유의 포인트 (리뷰 관점)."""
    out = []
    v = lambda k: (f.get(k) or {}).get("value")
    vd = val_date or dt.date.today().isoformat()
    src = [{"rcept_no": rep["rcept_no"], "date": rep["date"], "report_nm": rep["report_nm"]}] if rep else []

    def add(level, title, why, cat="조건", items=None):
        out.append({"level": level, "title": title, "why": why, "items": items if items is not None else src, "cat": cat})

    if rep and rep["period"] != vd:
        add("info", f"조건·잔액은 {rep['report_nm']} 기준 ({rep['period']})",
            "평가기준일과 보고서 기준일이 다릅니다. 그 사이 전환·상환·추가 부여·상실이 있었는지 확인하세요.", "회차")
    if kind in ("RCPS", "CPS"):
        if v("발행일") and v("발행일") > vd:
            add("bad", "평가기준일 이후 발행된 회차", f"발행일 {v('발행일')} > 평가기준일 {vd}. 평가 대상인지 확인하세요.", "회차")
        acc = (fa or {}).get("acc") or {}
        re_ = (acc.get("이익잉여금") or (None,))[0]
        if kind == "RCPS" and re_ is not None and re_ < 0:
            add("warn", f"결손 상태 (이익잉여금 {re_ / 1e8:,.0f}억) — 상환재원 없음",
                "상환주식은 배당가능이익 한도에서만 상환할 수 있습니다(상법 제345조). 대시보드는 배당가능초일(Temp AW)을 만기일로 두어 "
                "만기 전 상환청구를 막았습니다 (Temp 입력례). 평가사가 상환권(Put)을 어떻게 반영했는지 확인하세요.", "규제")
        if v("상하향조정") == "공모가조정":
            pct = v("공모가하한")
            add("info", f"IPO 공모가 기준 전환가 조정 ({pct:.0%})" if pct else "IPO 공모가 기준 전환가 조정",
                "Temp는 '공모가조정'으로 두고 격자에 리픽싱을 넣지 않습니다. 평가사가 IPO 확률·시나리오로 반영했는지, "
                "반영했다면 근거(상장 예정 시기·공모가 추정)를 확인하세요.")
        bet = (raw or {}).get("배당") or ""
        if re.search(r"누적", bet):
            add("info", "누적적·참가적 우선배당",
                f"'{bet[:60]}' — Temp 관행은 표면R 0%. 미지급 누적배당이 상환가액·잔여재산분배에 더해지는지 계약서를 확인하세요.")
        if (raw or {}).get("기타"):
            add("warn", "옵션·기타 약정 (발행 공시)",
                f"{(raw['기타'])[:220]} — 우선인수권·동반매도·풋/콜·투자금 용도 위반 시 상환 등 약정이 평가(조기상환·Call)에 영향이 있는지 확인하세요.")
        if (raw or {}).get("콜"):
            add("warn", "매도청구권(Call) 조항이 보입니다",
                "주석 조건표에 매도청구·콜옵션 표현이 있습니다. Call 초일·말일·한도·행사자(최대주주 등)를 계약서로 확인해 입력하세요.")
        add("info", "회계 분류 — 전환권·상환권",
            "전환가가 조정되는 조건(IPO·희석화)이 있으면 '확정 수량 교환'이 아니어서 파생상품부채(FVTPL)로 분리하는 것이 일반적입니다 "
            "(K-IFRS 1032·1109). 회사 주석의 분류와 평가사 FV 범위(주계약·파생 구분)를 맞춰 보세요.", "감사")
    if kind == "OPT":
        add("info", "풋·콜옵션 — 계약서 조건으로 입력",
            "행사가격·기산일·행사기간·수량·권리자/의무자를 계약서(주주간 계약·투자계약)로 확인해 넣으세요. "
            + ("주석 약정 문장: " + (raw or {}).get("문장", "")[:160] if (raw or {}).get("문장") else ""))
        add("info", "회계 — 의무자에 따라 분류",
            "회사가 자기지분상품(또는 종속기업 비지배지분)을 사 주어야 하는 풋은 상환금액 현재가치로 금융부채를 인식합니다(K-IFRS 1032 문단 23). "
            "최대주주 등 제3자 사이 옵션은 회사 재무제표 밖일 수 있습니다. 파생상품(FVTPL)인지, 부채(상각후원가)인지 확인하세요.", "감사")
    if kind == "SO":
        if v("행사종료일") and v("행사종료일") < vd:
            add("bad", f"행사기간 종료 ({v('행사종료일')}) — 평가 대상 아님",
                "평가기준일 전에 행사기간이 끝났습니다. 미행사분이 소멸했는지(기말 수량 0) 확인하고, 다른 차수를 고르세요.", "회차")
        if v("결제방식") == "현금결제형":
            add("warn", "현금결제형 주식선택권 — 매 보고기간말 재측정",
                "현금결제형은 평가기준일 공정가치로 부채를 재측정합니다. 측정일을 평가기준일로 두고 계산합니다.", "감사")
        else:
            add("info", "주식결제형 — 부여일 공정가치로 측정 (재측정 없음)",
                f"측정일은 부여일({v('부여일')})입니다. 평가기준일과 상관없이 부여일 가정으로 다시 계산해 회사 공정가치와 대조합니다.", "감사")
        if v("회사S") and v("행사가격") and v("회사S") > v("행사가격") * 1.0001:
            add("warn", f"행사가격({v('행사가격'):,.0f}) < 부여일 주가({v('회사S'):,.0f}) — 할인 부여",
                "상법 제340조의2: 신주 교부형 행사가격은 부여일 기준 실질가액 이상이어야 합니다(비상장은 평가가액). "
                "벤처기업 특례(벤처기업법 제16조의3, 시가 미만 부여 허용)에 해당하는지, 부여 당시 주식가치 산정 근거와 주총 결의를 확인하세요.",
                "규제")
        if re.search(r"주가|시장|TSR|상장", v("가득조건") or ""):
            add("warn", "시장성과조건이 있는 가득조건",
                f"가득조건 '{v('가득조건')}' — 주가·상장 조건은 공정가치에 반영해야 합니다(몬테카를로 등). 이항모형 단순 적용이 맞는지 확인하세요.")
        model = (raw or {}).get("모형")
        if model:
            add("info", f"회사 평가모형: {model}", "대시보드는 Temp SO 시트와 같은 이항모형(행사기간 중 조기행사)으로 다시 계산합니다.")
        if v("회사만기") and v("행사종료일") and v("회사만기") != v("행사종료일"):
            add("info", f"회사는 옵션만기를 {v('회사만기')}로 둠 (행사종료일 {v('행사종료일')})",
                "기대존속기간을 짧게 잡았는지 확인하세요. 대시보드는 행사종료일까지 계산합니다.")
    return out


def _filing_alerts(kind, fields, items, val_date, rep):
    """보고서 밖 공시로 확인할 점 — 보고서 기준일 이후 부여, IPO 공모가(공모가 리픽싱), 풋·콜 약정이 적히는 지분 공시."""
    out = []
    vd = val_date or dt.date.today().isoformat()
    per = (rep or {}).get("period") or ""
    v = lambda k: (fields.get(k) or {}).get("value")
    disc = lambda ds: [{"rcept_no": d["rcept_no"], "date": dart._iso(d["rcept_dt"]), "report_nm": d["report_nm"].strip()} for d in ds[:6]]
    if kind == "SO" and per:
        g = [d for d in items if re.search(r"주식매수선택권\s*부여", d["report_nm"]) and per < dart._iso(d["rcept_dt"]) <= vd]
        if g:
            out.append({"level": "warn", "cat": "회차", "title": f"보고서 기준일({per}) 이후 주식선택권 부여 공시 {len(g)}건",
                        "why": "주석에 아직 없는 차수입니다. 부여일·행사가·수량·행사기간을 공시로 확인해 평가 대상에 넣었는지 보세요.", "items": disc(g)})
    if kind in ("RCPS", "CPS") and v("상하향조정") == "공모가조정":
        ipo = [d for d in items if re.search(r"증권\s*신고서\s*\(\s*지분|투자\s*설명서|발행\s*조건\s*확정", d["report_nm"])
               and dart._iso(d["rcept_dt"]) <= vd]
        if ipo:
            out.append({"level": "warn", "cat": "회차", "title": f"IPO·공모 증권신고서·투자설명서 {len(ipo)}건 — 공모가 확인",
                        "why": "공모가가 정해졌으면 '공모가 × 하한%'로 전환가가 조정됐는지(전환주식의 전환가액 조정 공시) 확인하세요. "
                               "상장 뒤에는 Temp '공모가조정' 대신 조정된 전환가로 평가합니다.", "items": disc(ipo)})
    if kind == "OPT":
        sh = [d for d in items if re.search(r"대량\s*보유\s*상황|타법인\s*주식\s*및\s*출자증권|주주\s*간\s*계약|풋\s*옵션|콜\s*옵션", d["report_nm"])
              and dart._iso(d["rcept_dt"]) <= vd]
        if sh:
            out.append({"level": "info", "cat": "회차", "title": f"풋·콜 약정이 적힐 수 있는 공시 {len(sh)}건",
                        "why": "5% 보고서의 '보유주식등에 관한 주요계약', 타법인주식 양수도 결정의 '기타 투자판단 참고사항'에 콜·풋·우선매수권 약정이 적힙니다. "
                               "계약 상대방·행사가·기간이 평가 조건과 맞는지 확인하세요.", "items": disc(sh)})
    return out


def load_note_issue(corp_code, rcept_no, val_date, kind, key):
    """정기보고서 주석의 RCPS·CPS·SO 한 건을 불러온다."""
    items = dart.disclosures(corp_code)
    rep = next((r for r in notes.periodic_reports(items) if r["rcept_no"] == rcept_no), None)
    raw_src, fields = notes.find_instrument(rcept_no, kind, key)
    if fields is None:
        raise ValueError(f"보고서에서 '{key}'를 찾지 못했습니다.")
    raw = fields.pop("_raw", {}).get("value") or {}
    if key == "RIGHTS" and fields.get("Put초일", {}).get("value") and not fields.get("YTP", {}).get("value"):
        try:
            sib = notes.note_instruments(corp_code, val_date, items)
            rates = set()
            for x in sib["items"]:
                if x["kind"] == "RCPS" and x["key"] != "RIGHTS":
                    _, ff = notes.find_instrument(x["rcept_no"], x["kind"], x["key"])
                    if ff and ff.get("YTP", {}).get("value"):
                        rates.add((ff["YTP"]["value"], ff["YTP복리"]["value"]))
            if len(rates) == 1:
                y, comp = rates.pop()
                why = (f"이 공시에는 상환 이율이 없어 {sib['report']['report_nm']}의 다른 회차 이율({y:.2%} {comp})을 가정했습니다. "
                       "계약서로 확인하세요.")
                for k2, v2 in (("YTP", y), ("만기이자율", y), ("YTP복리", comp), ("상환활증방식", comp)):
                    fields[k2] = {"value": v2, "evidence": [], "conf": "low", "note": why}
        except Exception:
            pass
    warnings = []
    try:
        fa = dart.fin_audit(corp_code, val_date)
    except Exception as ex:
        fa, _ = {}, warnings.append(f"재무·감사 정보를 읽지 못했습니다: {ex}")
    acc = (fa or {}).get("acc") or {}
    re_ = (acc.get("이익잉여금") or (None,))[0]
    if kind == "RCPS" and re_ is not None and re_ < 0 and fields.get("Put초일", {}).get("value"):
        fields["배당가능초일"] = {"value": fields["만기일"]["value"], "evidence": [], "conf": "low",
                              "note": f"{fa.get('year')} 이익잉여금 {re_ / 1e8:,.0f}억(결손) → 배당가능이익이 없어 만기 전 상환 불가로 보고 "
                                      "만기일로 둡니다 (Temp 실무 입력례: 결손이면 배당가능초일 = 만기일). 평가사 가정을 확인해 고치세요."}
    ev_alerts = []
    if kind in ("RCPS", "CPS"):
        evs = notes.rcps_events(items, fields.get("발행일", {}).get("value"), val_date)
        notes.apply_rcps_events(fields, evs, val_date)
        try:
            notes.apply_pref_call(fields, notes.pref_call_text(rcept_no, raw), rcept_no)
        except Exception:
            pass
        for e in evs:
            tag = " (평가기준일 이후 · 후속사건)" if e.get("after_vd") else ""
            it_ = [{"rcept_no": e["rcept_no"], "date": e["date"], "report_nm": e["report_nm"]}]
            if e["type"] == "refix":
                ev_alerts.append({"level": "info", "cat": "회차", "title": f"{e['date']} 전환가 조정 {e['before']:,.0f} → {e['after']:,.0f}원{tag}",
                                  "why": "현 전환가·리픽싱 조건(주기·하한·상향 여부)에 반영했는지 확인하세요.", "items": it_})
            elif e["type"] == "conversion":
                ev_alerts.append({"level": "info", "cat": "회차", "title": f"{e['date']} 보통주 전환 {e['converted']:,.0f}주 → 잔여 {e['after_sh']:,.0f}주{tag}",
                                  "why": "잔여 우선주 수(잔여총액)에 반영됐는지 확인하세요.", "items": it_})
            else:
                who = f"{e.get('who') or ''}({e.get('rel') or ''})"
                ev_alerts.append({"level": "warn", "cat": "규제",
                                  "title": f"{e['date']} Call {'행사' if e['type'] == 'call_exercise' else '행사자 지정'} — {who} {e.get('qty') or 0:,.0f}주{tag}",
                                  "why": "Call 한도·행사자(최대주주 등 지분율 한도)·남은 Call 물량을 평가에 맞게 넣었는지 확인하세요. "
                                         "행사된 우선주는 소멸하지 않고 행사자에게 넘어갑니다.", "items": it_})
    if kind == "SO":
        for b in notes.so_exercise_blocks(items, val_date):
            if b["부여일"] == fields.get("부여일", {}).get("value") and b["잔여"] is not None and (not rep or b["date"] > rep["period"]):
                fields["잔여수량"] = {"value": b["잔여"], "evidence": [dart.ev(b["rcept_no"], "잔여주식수", f"{b['잔여']:,.0f}")], "conf": "high",
                                  "note": f"{b['date']} '{b['report_nm']}'의 잔여 주식수 (보고서 이후 행사·취소 반영)"}
                ev_alerts.append({"level": "info", "cat": "회차", "title": f"{b['date']} 주식선택권 행사 공시 — 잔여 {b['잔여']:,.0f}주",
                                  "why": "보고서 기준일 이후 행사·취소가 있었습니다. 잔여 수량을 반영했습니다.",
                                  "items": [{"rcept_no": b["rcept_no"], "date": b["date"], "report_nm": b["report_nm"]}]})
                break
    ev_alerts += _filing_alerts(kind, fields, items, val_date, rep)
    al, summary = dart.alerts(corp_code, fields, items, val_date, [], fa, "CB" if kind not in ("SO", "OPT") else kind)
    al = [a for a in al if a["cat"] != "조건"]
    al = ev_alerts + _note_alerts(kind, fields, raw, fa, val_date, rep) + al
    order = {"bad": 0, "warn": 1, "info": 2}
    al.sort(key=lambda x: (order[x["level"]], {"회차": 0, "규제": 1, "재무": 2, "감사": 2, "조건": 3, "공시": 4}.get(x["cat"], 5)))
    _check_evidence(fields)
    issue = {"kind": kind, "rcept_no": rcept_no, "key": key, "bd_tm": fields["회차"]["value"], "note": True,
             "src": f"{rep['report_nm']} 주석" if rep else "정기보고서 주석"}
    disc = {d["rcept_no"]: d for d in items}
    out_disc = [{"rcept_no": d["rcept_no"], "date": d["rcept_dt"], "report_nm": d["report_nm"].strip()} for d in items[:300]]
    if rcept_no not in disc:
        out_disc.insert(0, {"rcept_no": rcept_no, "date": rep["date"] if rep else "", "report_nm": rep["report_nm"] if rep else ""})
    return {"issue": issue, "fields": fields, "alerts": al, "summary": summary, "history": [], "warnings": warnings,
            "actions": [], "disclosures": out_disc, "report": rep, "raw": raw,
            "related": notes.related(items, "PUT" if kind == "OPT" else kind)}


def load_issue(corp_code, rcept_no, val_date=None, kind=None, key=None):
    """회차 하나를 불러온다: 발행조건 파싱 → 평가기준일까지의 조정·잔액 이력 반영 → 재무·감사 → 유의 포인트."""
    if kind in ("RCPS", "CPS", "SO", "OPT"):
        return load_note_issue(corp_code, rcept_no, val_date, kind, key)
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
    al, summary = dart.alerts(corp_code, fields, items, val_date, hist, fa, it["kind"], issue=it, issues=issues)
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
    _check_evidence(fields)
    return {"issue": {k: v for k, v in it.items() if k != "raw"}, "fields": fields, "alerts": al, "summary": summary,
            "history": hist, "warnings": warnings, "actions": acts, "related": notes.related(items, it["kind"]),
            "disclosures": [{"rcept_no": d["rcept_no"], "date": d["rcept_dt"], "report_nm": d["report_nm"].strip()} for d in items[:300]]}


def _flat(c):
    """곡선 칸에 값이 하나만 있으면 그 금리로 평평한 곡선을 만든다 (주석의 '무위험이자율 3.35%' 같은 단일값)."""
    xs = [x for x in (c or []) if x not in (None, "")]
    return [xs[0]] * 16 if len(xs) == 1 else c


def so_params(st):
    """주식선택권 계산 입력. 주식결제형은 부여일, 현금결제형은 평가기준일이 측정일."""
    f = {k: (v or {}).get("value") for k, v in st["fields"].items()}
    m = st["meta"]
    cash = f.get("결제방식") == "현금결제형"
    measure = m.get("val_date") if cash else (f.get("부여일") or m.get("val_date"))
    need = {"측정일(부여일)": measure, "행사가격": f.get("행사가격"), "행사종료일": f.get("행사종료일"),
            "주가": st["market"].get("S"), "변동성": st["market"].get("sigma")}
    miss = [k for k, v in need.items() if v in (None, "", 0)]
    if miss:
        raise ValueError("계산에 필요한 값이 비어 있습니다: " + ", ".join(miss))
    rf = _flat(st["curves"]["rf"])
    if len([x for x in rf if x not in (None, "")]) < 1:
        raise ValueError("Rf를 넣어 주세요 (주석의 무위험이자율 하나만 넣어도 됩니다).")
    return dict(val_date=measure, maturity=f["행사종료일"], ex_start=f.get("행사시작일"), ex_end=f["행사종료일"],
                S=st["market"]["S"], strike=f["행사가격"], sigma=st["market"]["sigma"], div_yield=f.get("기대배당") or 0,
                rf_curve=rf, steps_per_year=m.get("steps_per_year") or 52)


def opt_discount(st):
    """Temp SO 시트: SO후속 = IF(옵션유형="Call","N","Y") → Put은 Rd, Call은 Rf로 할인. 사용자가 직접 고른 값이 있으면 그 값."""
    fd = (st.get("fields") or {}).get("할인") or {}
    typ = ((st.get("fields") or {}).get("옵션종류") or {}).get("value") or "Put"
    if fd.get("manual") and fd.get("value"):
        return fd["value"]
    return "Rf" if typ == "Call" else "Rd"


def value_opt(st):
    """풋·콜옵션 (주식 대상) — Temp SO 시트 방식: 행사기간 안에서만 조기행사, 할인 Rf 또는 Rd(SO후속)."""
    f = {k: (v or {}).get("value") for k, v in st["fields"].items()}
    m = st["meta"]
    need = {"평가기준일": m.get("val_date"), "행사가격": f.get("행사가격"), "행사종료일": f.get("행사종료일"),
            "주가": st["market"].get("S"), "변동성": st["market"].get("sigma")}
    miss = [k for k, v in need.items() if v in (None, "", 0)]
    if miss:
        raise ValueError("계산에 필요한 값이 비어 있습니다: " + ", ".join(miss))
    rf, rd = _flat(st["curves"]["rf"]), _flat(st["curves"]["rd"])
    disc = opt_discount(st)
    if disc == "Rd" and len([x for x in rd if x not in (None, "")]) < 1:
        raise ValueError("할인을 Rd로 골랐습니다. Rd 곡선(또는 할인율 하나)을 넣어 주세요.")
    p = dict(val_date=m["val_date"], maturity=f["행사종료일"], ex_start=f.get("행사시작일"), ex_end=f["행사종료일"],
             S=st["market"]["S"], strike=f["행사가격"], sigma=st["market"]["sigma"], rf_curve=rf, rd_curve=rd, discount=disc,
             option_type=f.get("옵션종류") or "Put", strike_rate=f.get("행사가증가율") or 0, strike_comp=f.get("행사가복리") or "연복리",
             strike_base=f.get("행사가기산일") or None, steps_per_year=m.get("steps_per_year") or 52)
    out = engine.so_value(p)
    per, qty = out["result"]["옵션1개당"], f.get("수량")
    out["result"].update({"잔여수량": qty, "전체가치": per * qty if qty else None})
    out["info"]["measure_date"] = m["val_date"]
    return out


def value_so(st):
    out = engine.so_value(so_params(st))
    f = {k: (v or {}).get("value") for k, v in st["fields"].items()}
    per = out["result"]["옵션1개당"]
    qty_g, qty_l = f.get("부여수량"), f.get("잔여수량")
    out["result"].update({"부여수량": qty_g, "잔여수량": qty_l,
                          "전체가치": per * (qty_l if qty_l is not None else (qty_g or 0)),
                          "부여분 총액": per * qty_g if qty_g else None})
    out["info"]["measure_date"] = so_params(st)["val_date"]
    return out


def engine_params(st):
    f = {k: (v or {}).get("value") for k, v in st["fields"].items()}
    m = st["meta"]
    st = dict(st, curves={"rf": _flat(st["curves"]["rf"]), "rd": _flat(st["curves"]["rd"])})
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
        put_start=(max(f["Put초일"], f["배당가능초일"]) if f.get("Put초일") and f.get("배당가능초일") else f.get("Put초일")),
        put_end=f.get("Put말일"), put_freq=f.get("Put연회수") or 12,
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


def peers(codes, val_date, period="1년", maturity=None, window=180):
    """유사기업(상장) 변동성 — 비상장 평가용. Temp DW proxy1~5(FA~FE)·pcode1~5(FM~FQ)와 같은 방식: 각 사 변동성의 단순평균."""
    by_code = {c["stock_code"]: c for c in dart.corps() if c.get("stock_code")}
    rows = []
    for code in [c for c in re.split(r"[\s,;/]+", codes) if c][:12]:
        code = code.zfill(6)
        c = by_code.get(code)
        mk = None
        if c:
            try:
                mk = dart.company(c["corp_code"]).get("corp_cls")
            except Exception:
                mk = None
        try:
            r = market.stock(code, val_date, mk, period, maturity, window, ras=ras)
            rows.append({"code": code, "name": (c or {}).get("corp_name") or r.get("name") or code, "vol": r["vol"],
                         "vol_iqr": (r.get("vol_iqr") or {}).get("vol"), "n_year": r["n_year"], "price_date": r["price_date"],
                         "source": r["source"]})
        except Exception as ex:
            rows.append({"code": code, "name": (c or {}).get("corp_name") or code, "error": str(ex)})
    ok = [x for x in rows if x.get("vol")]
    avg = sum(x["vol"] for x in ok) / len(ok) if ok else None
    ok2 = [x for x in ok if x.get("vol_iqr")]
    avg2 = sum(x["vol_iqr"] for x in ok2) / len(ok2) if ok2 else None
    return {"rows": rows, "avg": avg, "avg_iqr": avg2, "n": len(ok)}


JSON = "application/json; charset=utf-8"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def borrow(corp_code, val_date):
    """평가기준일에 맞는 정기보고서 주석의 차입금 이자율 (Rd 참고값)."""
    items = dart.disclosures(corp_code)
    rep = notes.pick_report(items, val_date)
    if not rep:
        return {"report": None, "n": 0, "items": []}
    return {"report": rep, **notes.borrow_rates(rep["rcept_no"])}


def implied_for(st):
    """회차 리뷰: 발행시점 역산 스프레드. 발행일 이론가치(최초 전환가·발행총액) = 발행가가 되는 Rd − Rf를 구해
    평가기준일 Rf에 더한 Rd 곡선을 돌려준다 (등급을 모르거나 평가사 등급을 점검할 때)."""
    import copy
    m = st["meta"]
    v = {k: (x or {}).get("value") for k, x in st["fields"].items()}
    iss, mat, vd = v.get("발행일"), v.get("만기일"), m["val_date"]
    if not iss or not mat:
        raise ValueError("발행일·만기일이 필요합니다.")
    md = min(iss, vd)
    f0 = copy.deepcopy(st["fields"])
    f0["현조정행사가격"] = {"value": v.get("최초전환가격") or v.get("현조정행사가격")}
    target = v.get("발행총액") or v.get("잔여총액")
    f0["잔여총액"] = {"value": target}
    notes_ = []
    if m.get("stock_code"):
        mk = market.stock(m["stock_code"], md, m.get("market"), ras=ras)
        S, sig, ssrc = mk["price"], mk["vol"], f"{mk['price_date']} 주가 {mk['price']:,.0f}원 · 변동성 {mk['vol']:.1%} ({mk['source']})"
    else:
        S = v.get("최초전환가격") or v.get("주당발행가") or v.get("현조정행사가격")
        sig = st["market"].get("sigma")
        if not sig:
            raise ValueError("비상장 — 변동성(유사기업)을 먼저 넣어 주세요.")
        ssrc = f"비상장 — 발행일 주가 = 최초 전환가 {S:,.0f}원 가정 · 변동성 {sig:.1%}(현재 입력값)"
        notes_.append("비상장은 발행가가 공정가치(제3자 거래)라는 전제로 주가를 최초 전환가로 둡니다.")
    rf_now = _flat(st["curves"]["rf"])
    if ras is not None:
        c0 = ras.curves(md, None)
        rf0, rsrc = c0["rf"], f"{c0['rf_date']} 국고채"
    else:
        rf0, rsrc = rf_now, "평가기준일 Rf로 근사 (외부 웹은 날짜별 곡선 없음)"
    s, why = screen.implied_spread(f0, iss, m.get("kind") or "CB", S, sig, rf0, target)
    out = {"issue_date": iss, "mkt_date": md, "stock": ssrc, "rf_src": rsrc, "spread": s * 100 if s is not None else None,
           "why": why, "notes": notes_}
    if s is not None and len([x for x in rf_now if x not in (None, "")]) >= 2:
        out["rd"] = screen.rd_from_spread(rf_now, s)
    rd_now = _flat(st["curves"].get("rd") or [])
    if len([x for x in rd_now if x not in (None, "")]) >= 2 and len([x for x in rf_now if x not in (None, "")]) >= 2:
        T = engine.yf(dt.date.fromisoformat(vd), dt.date.fromisoformat(mat))
        out["grade_spread"] = screen._par(engine.RD_TENORS, rd_now, T) - screen._par(engine.RF_TENORS, rf_now, T)
        out["T"] = T
    return out


def book_check(st):
    """장부 대조: 정기보고서 주석의 파생상품부채(공정가치)·사채 장부금액·평가 가정을 읽고,
    같은 보고기간 말 시장자료로 대시보드가 다시 계산한 값과 비교한다."""
    m = st["meta"]
    v = {k: (x or {}).get("value") for k, x in st["fields"].items()}
    kind = m.get("kind") or "CB"
    items = dart.disclosures(m["corp_code"])
    rep = notes.pick_report(items, m["val_date"])
    if not rep:
        raise ValueError("평가기준일 이전 정기보고서가 없습니다.")
    per = rep["period"]
    if v.get("발행일") and v["발행일"] > per:
        return {"report": rep, "period": per, "book": None, "why": f"{rep['report_nm']} 기준일({per}) 뒤에 발행된 회차라 장부 값이 아직 없습니다."}
    bk = notes.book_values(rep["rcept_no"], kind, v.get("회차") or m.get("bd_tm"), v.get("발행총액"))
    out = {"report": rep, "period": per, "book": bk, "notes": []}
    if m.get("stock_code"):
        mk = market.stock(m["stock_code"], per, m.get("market"), ras=ras)
        S, sig, msrc = mk["price"], mk["vol"], f"{mk['price_date']} 주가 {mk['price']:,.0f}원 · 변동성 {mk['vol']:.1%}"
    else:
        S, sig, msrc = st["market"].get("S"), st["market"].get("sigma"), "비상장 — 화면의 주가·변동성 사용"
    grade = m.get("credit_grade") or None
    if ras is not None and grade:
        c = ras.curves(per, grade)
        rf, rd, csrc = c["rf"], c["rd"], f"{c['rf_date']} 국고채 · {grade}"
    else:
        rf, rd, csrc = _flat(st["curves"]["rf"]), _flat(st["curves"]["rd"]), "화면의 Rf·Rd 곡선 (평가기준일 기준)"
        if per != m["val_date"]:
            out["notes"].append("곡선은 보고기간 말이 아니라 화면 값입니다 (외부 웹 또는 등급코드 없음).")
    if per != m["val_date"]:
        out["notes"].append(f"잔액·전환가는 평가기준일({m['val_date']}) 기준 값입니다 — 그 사이 전환·리픽싱이 있으면 차이가 날 수 있습니다.")
    st2 = {"meta": dict(m, val_date=per), "fields": st["fields"], "market": {"S": S, "sigma": sig}, "curves": {"rf": rf, "rd": rd}}
    res = engine.value(engine_params(st2))["result"]
    out.update({"market": msrc, "curves": csrc, "S": S, "sigma": sig,
                "calc_total": res.get("전체가치"), "calc_bond": res.get("일반사채"),
                "calc_deriv": (res.get("전체가치") or 0) - (res.get("일반사채") or 0)})
    if kind in ("RCPS", "CPS"):
        out["calc_compare"] = out["calc_total"]
        out["compare_label"] = "종류주식 부채 (전체 공정가치)"
    else:
        out["calc_compare"] = out["calc_deriv"]
        out["compare_label"] = "파생상품부채 (전체가치 − 일반사채)"
    b = bk.get("derivative")
    if b is not None and out["calc_compare"] is not None and b:
        out["gap"] = out["calc_compare"] - b
        out["gap_pct"] = out["gap"] / abs(b)
        if kind not in ("RCPS", "CPS") and abs(out["gap_pct"]) > 0.05:
            out["book_spread"] = _book_spread(st2, rf, b)
    return out


def _book_spread(st2, rf, target):
    """장부 파생상품부채(전체 − 일반사채)를 맞추는 Rd − Rf. 파생 = 전체 − 일반사채는 스프레드가 클수록 커진다."""
    def deriv(s):
        st3 = dict(st2, curves={"rf": rf, "rd": screen.rd_from_spread(rf, s)})
        r = engine.value(engine_params(st3))["result"]
        return (r.get("전체가치") or 0) - (r.get("일반사채") or 0)
    lo, hi = 0.0, 0.6
    flo, fhi = deriv(lo) - target, deriv(hi) - target
    if flo * fhi > 0:
        return None
    for _ in range(30):
        mid = (lo + hi) / 2
        fm = deriv(mid) - target
        if abs(fm) <= abs(target) * 1e-3 or hi - lo < 1e-4:
            return mid * 100
        if (fm > 0) == (fhi > 0):
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2 * 100


def screen_corp(body):
    """딜 스크리닝: 회사 하나의 기간 안 발행 회차 평가 (화면이 회사별로 차례로 부른다)."""
    cv = screen.Curves(ras, _flat(body.get("rf") or []) or None, _flat(body.get("rd") or []) or None) if ras is None \
        else screen.Curves(ras)
    key = ("screen", body["corp"]["corp_code"], body["val_date"], body["since"], tuple(body.get("kinds") or ()),
           json.dumps(body.get("credit") or {}, sort_keys=True), bool(body.get("at_issue")), bool(body.get("book")),
           json.dumps([body.get("rf"), body.get("rd")]) if ras is None else "")
    return screen._memo(key, lambda: screen.value_corp(body["corp"], body["val_date"], body["since"], tuple(body.get("kinds") or ("CB", "BW", "EB")),
                                                       body.get("credit"), cv, bool(body.get("at_issue")), ras,
                                                       bool(body.get("book"))), ttl=1800)


def route(method, path, query="", body=None):
    """API 한 건 처리 → (상태코드, 내용, 콘텐츠타입, 추가헤더)."""
    q = {k: v[0] for k, v in parse_qs(query or "").items()}
    if method == "GET":
        if path == "/api/search":
            return 200, dart.search(q.get("q")), JSON, None
        if path == "/api/issues":
            return 200, issues_for(q["corp_code"], q.get("val_date")), JSON, None
        if path == "/api/load":
            return 200, load_issue(q["corp_code"], q["rcept_no"], q.get("val_date"), q.get("kind"), q.get("key")), JSON, None
        if path == "/api/peers":
            return 200, peers(q.get("codes") or "", q["val_date"], q.get("period") or "1년", q.get("maturity") or None,
                              int(q.get("window", 180))), JSON, None
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
        if path == "/api/borrow":
            return 200, borrow(q["corp_code"], q.get("val_date")), JSON, None
        if path == "/api/screen/universe":
            kinds = tuple((q.get("kinds") or "CB,BW,EB").split(","))
            return 200, screen.universe(q["since"], q.get("until"), kinds), JSON, None
        if path == "/api/screen/info":
            return 200, {"ras": ras is not None}, JSON, None
        if path == "/api/latest":
            return 200, dart.latest(q["corp_code"], int(q.get("days", 45))), JSON, None
    if method == "POST":
        st = json.loads(body or "{}")
        if path == "/api/bookcheck":
            return 200, book_check(st), JSON, None
        if path == "/api/implied":
            return 200, implied_for(st), JSON, None
        if path == "/api/screen/corp":
            return 200, screen_corp(st), JSON, None
        if path == "/api/value":
            if st["meta"].get("kind") == "SO":
                return 200, value_so(st), JSON, None
            if st["meta"].get("kind") == "OPT":
                return 200, value_opt(st), JSON, None
            out = engine.value(engine_params(st))
            if st["meta"].get("issue_basis"):
                out["result"] = engine.implied_conv_at_issue(out["result"], float(st["fields"]["잔여총액"]["value"]))
            return 200, out, JSON, None
        if path in ("/api/export/review", "/api/export/dw"):
            m = st["meta"]
            if m.get("kind") == "OPT":
                m["discount"] = opt_discount(st)
            ymd = dt.date.today().strftime("%y%m%d")
            if path.endswith("review"):
                data, name = export.review_xlsx(st), f"{m['company']}_FV_review_RAS_{ymd}.xlsx"
            else:
                data, name = export.dw_xlsx(st), f"Temp_DW입력_{m['company']}_{m.get('bd_tm') or ''}{m.get('kind') or 'CB'}_{ymd}.xlsx"
            return 200, data, XLSX, {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"}
    return 404, {"error": "not found"}, JSON, None
