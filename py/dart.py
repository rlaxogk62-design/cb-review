"""DART 공시 수집 · 원문 파싱 · 잔액 추적 · 유의 포인트.

모든 자동 입력값은 근거(evidence)를 함께 돌려준다.
  evidence = {rcept_no, label(원문에서 먼저 찾을 항목명), text(형광펜 칠할 문구), src}
화면은 원문에서 label을 찾고 그 뒤에 나오는 text에 형광펜을 칠한다.
"""
import calendar
import datetime as dt
import html
import io
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "cache")
os.makedirs(CACHE, exist_ok=True)
API = "https://opendart.fss.or.kr/api"
DATE = r"(\d{4}\s*[-.]\s*\d{1,2}\s*[-.]\s*\d{1,2}|\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일?)"


def api_key():
    k = os.environ.get("DART_API_KEY")
    if k:
        return k.strip()
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            if line.startswith("DART_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError(".env 파일에 DART_API_KEY가 없습니다.")


def _get(path, **params):
    params["crtfc_key"] = api_key()
    r = requests.get(f"{API}/{path}", params=params, timeout=60)
    r.raise_for_status()
    return r


def _json(path, **params):
    j = _get(path, **params).json()
    if j.get("status") not in ("000", "013"):
        raise RuntimeError(f"DART {path}: {j.get('status')} {j.get('message')}")
    return j


def num(s):
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s in ("", "-"):
        return None
    m = re.search(r"-?\d+(\.\d+)?", s)
    return float(m.group()) if m else None


def kdate(s):
    """'2026년 11월 12일' · '2026-11-12' · '2026.11.12' · '20261112' → '2026-11-12'"""
    if not s:
        return None
    m = re.search(r"(\d{4})\s*[년.\-/]?\s*(\d{1,2})\s*[월.\-/]?\s*(\d{1,2})", str(s))
    if not m:
        return None
    try:
        return dt.date(*map(int, m.groups())).isoformat()
    except ValueError:
        return None


def add_months(iso, months):
    d = dt.date.fromisoformat(iso)
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1])).isoformat()


def months_between(a, b):
    a, b = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return (b.year - a.year) * 12 + (b.month - a.month) + (b.day - a.day) / 30.0


def ev(rcept_no, label, text, src="공시"):
    return {"rcept_no": rcept_no, "label": label, "text": text, "src": src}


_corps = None


def corps():
    global _corps
    if _corps is not None:
        return _corps
    p = os.path.join(DATA, "corp_codes.json")
    if not os.path.exists(p) or (dt.datetime.now().timestamp() - os.path.getmtime(p)) > 7 * 86400:
        z = zipfile.ZipFile(io.BytesIO(_get("corpCode.xml").content))
        root = ET.fromstring(z.read(z.namelist()[0]))
        rows = [{"corp_code": e.findtext("corp_code"), "corp_name": e.findtext("corp_name"),
                 "stock_code": (e.findtext("stock_code") or "").strip()} for e in root.iter("list")]
        json.dump(rows, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    _corps = json.load(open(p, encoding="utf-8"))
    return _corps


def search(q, limit=20):
    q = (q or "").strip()
    if not q:
        return []
    hits = [c for c in corps() if q in c["corp_name"] or q == c["stock_code"]]
    hits.sort(key=lambda c: (not c["stock_code"], c["corp_name"] != q, len(c["corp_name"])))
    return hits[:limit]


_company_cache = {}


def company(corp_code):
    """DART 기업개황 (주소·법인구분 등). 화면에는 필요한 값만 쓴다."""
    if corp_code not in _company_cache:
        j = _json("company.json", corp_code=corp_code)
        _company_cache[corp_code] = {k: j.get(k) for k in ("corp_name", "corp_cls", "stock_code", "adres", "est_dt", "acc_mt")}
    return _company_cache[corp_code]


def latest(corp_code, since_days=45):
    """최근 공시 목록 (새 공시 확인용). DART는 접수 즉시 목록에 올라온다."""
    end = dt.date.today()
    j = _json("list.json", corp_code=corp_code, bgn_de=(end - dt.timedelta(days=since_days)).strftime("%Y%m%d"),
              end_de=end.strftime("%Y%m%d"), page_count=100)
    return [{"rcept_no": d["rcept_no"], "date": _iso(d["rcept_dt"]), "report_nm": d["report_nm"].strip()} for d in j.get("list") or []]


def document_xml(rcept_no):
    if not re.fullmatch(r"\d{14}", str(rcept_no)):
        raise ValueError("접수번호 형식 오류")
    p = os.path.join(CACHE, f"{rcept_no}.xml")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read()
    z = zipfile.ZipFile(io.BytesIO(_get("document.xml", rcept_no=rcept_no).content))
    names = sorted(z.namelist(), key=lambda n: (len(n), n))
    raw = z.read(names[0])
    t = None
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            t = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if t is None:
        t = raw.decode("utf-8", "replace")
    open(p, "w", encoding="utf-8").write(t)
    return t


def document_html(rcept_no):
    """DART 원문을 화면에 띄울 HTML로 바꾼다. 스크립트·이벤트 속성은 지운다."""
    x = document_xml(rcept_no)
    m = re.search(r"<BODY[^>]*>(.*)</BODY>", x, re.S | re.I)
    body = m.group(1) if m else x
    body = re.sub(r"<script.*?</script>", "", body, flags=re.S | re.I)
    body = re.sub(r"<(iframe|object|embed|link|meta)[^>]*>", "", body, flags=re.I)
    body = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", body, flags=re.I)
    body = re.sub(r"(href|src)\s*=\s*(\"\s*javascript:[^\"]*\"|'\s*javascript:[^']*')", "", body, flags=re.I)
    body = body.replace("&cr;", "<br>")
    body = re.sub(r"<(/?)(TE|TU)(\s[^>]*)?>", r"<\1td>", body)
    body = re.sub(r"\s(WIDTH|HEIGHT)\s*=\s*\"?\d+\"?", "", body, flags=re.I)
    body = re.sub(r"<(/?)TITLE(\s[^>]*)?>", r"<\1h3>", body)
    body = re.sub(r"<PGBRK[^>]*/?>", "<hr>", body)
    body = re.sub(r"</?(LIBRARY|SECTION-\d|TABLE-GROUP|COMPANY-NAME|DOCUMENT-NAME|FORMULA-VERSION|EXTRACTION|COVER[^>]*)>", "", body)
    title = re.search(r"<DOCUMENT-NAME[^>]*>(.*?)</DOCUMENT-NAME>", x, re.S)
    css = ("body{font:13px/1.65 'Noto Sans KR','Malgun Gothic',sans-serif;color:#3E4757;margin:14px;background:#FDFCF9}"
           "table{border-collapse:collapse;width:100%!important;margin:8px 0;table-layout:auto!important}"
           "td,th{border:1px solid #D3CEC4;padding:4px 6px;vertical-align:top;width:auto!important;white-space:normal!important}"
           "col{width:auto!important}"
           "h2,h3{color:#15233B}mark.ev{background:#FFE45C;color:#15233B;padding:0 1px;border-radius:2px;box-shadow:0 0 0 2px #F5B700}"
           "mark.lab{background:#FFF1B3;color:inherit}")
    t = (title.group(1).strip() if title else "")
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head><body>"
            f"<h2>{t}</h2>{body}</body></html>")


def plain(rcept_no):
    t = document_xml(rcept_no)
    t = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
    t = t.replace("&cr;", " ")
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"\.xforms[^{}]*\{[^}]*\}", " ", t)
    return re.sub(r"\s+", " ", t)


PRICE_KEY = {"CB": "cv_prc", "BW": "ex_prc", "EB": "ex_prc"}
PERIOD_KEYS = {"CB": ("cvrqpd_bgd", "cvrqpd_edd"), "BW": ("expd_bgd", "expd_edd"), "EB": ("exrqpd_bgd", "exrqpd_edd")}
SHARES_KEY = {"CB": ("cvisstk_cnt", "cvisstk_tisstk_vs"), "BW": ("nstk_isstk_cnt", "nstk_isstk_tisstk_vs"),
              "EB": ("extg_stkcnt", "extg_tisstk_vs")}
LABEL = {"CB": ("전환가액", "전환청구기간"), "BW": ("행사가액", "권리행사기간"), "EB": ("교환가액", "교환청구기간")}


def cb_list(corp_code):
    out = []
    for kind, path in (("CB", "cvbdIsDecsn.json"), ("BW", "bdwtIsDecsn.json"), ("EB", "exbdIsDecsn.json")):
        try:
            j = _json(path, corp_code=corp_code, bgn_de="20150101", end_de=dt.date.today().strftime("%Y%m%d"))
        except Exception:
            continue
        for r in j.get("list", []) or []:
            out.append({"kind": kind, "rcept_no": r["rcept_no"], "bd_tm": (r.get("bd_tm") or "").strip(),
                        "corp_cls": r.get("corp_cls"),
                        "bd_knd": r.get("bd_knd"), "bd_fta": r.get("bd_fta"), "bddd": r.get("bddd"),
                        "price": r.get(PRICE_KEY[kind]), "mtd": r.get("bd_mtd"), "raw": r})
    out.sort(key=lambda r: r["rcept_no"], reverse=True)
    return out


def sections(p):
    """원문 평문을 Put 구간 / Call 구간으로 나눈다.
    'Put Option'·'조기상환청구권(Put Option)' 머리부터 다음 Call 머리(최대 3,500자)까지가 Put 구간이고, Call도 같다.
    '※ 세부내용은 ... 참고' 같은 안내 문장(소문자 option)은 머리로 보지 않는다."""
    heads = []
    for mm in re.finditer(r"Put Option|조기상환청구권에\s*관한", p):
        heads.append((mm.start(), "put"))
    for mm in re.finditer(r"Call Option|매도청구권에\s*관한", p):
        heads.append((mm.start(), "call"))
    heads.sort()
    puts, calls = [], []
    for k, (pos, kind) in enumerate(heads):
        nxt = next((q for q, kd in heads[k + 1:] if kd != kind and q > pos + 30), None)
        end = min(nxt if nxt is not None else len(p), pos + 3500)
        stop = re.search(r"\s(?:다|라)\.\s|\s\d{2}\.\s[가-힣]|\[발행회사의", p[pos + 20:end])
        if stop:
            end = pos + 20 + stop.start()
        (puts if kind == "put" else calls).append(p[pos:end])
    return puts, calls


TABLE_ROW = re.compile(rf"((?:\d+\s*차\s+)?(?:{DATE}[\s:]+){{1,3}}(?:[가-힣()]{{0,12}}의\s*)?)(\d{{2,3}}\.\d+)\s*%")


def exercise_table(sec):
    """행사 일정표 → [(지급일, 상환율, 원문)]. 아래 형식을 모두 읽는다.
      '1차 2025-12-13 2025-12-29 2026-01-12 102.3532%'   (청구기간 → 지급일 순)
      '2030-10-27 2030-08-28 2030-09-27 100.0000%'       (지급일 → 청구기간 순)
      '2023-11-26 : 권면금액의 100.0000%'
    청구기간은 지급일보다 앞서므로 한 줄에서 가장 늦은 날짜를 지급일로 본다."""
    rows = []
    for mm in TABLE_ROW.finditer(sec):
        ds = [kdate(x) for x in re.findall(DATE, mm.group(1))]
        ds = [d for d in ds if d]
        rate = float(mm.group(mm.lastindex))
        if not ds or not (90 <= rate <= 250):
            continue
        rows.append((max(ds), rate / 100, mm.group(0).strip()))
    seen, out = set(), []
    for r in sorted(rows):
        if r[0] not in seen:
            seen.add(r[0])
            out.append(r)
    return out if len(out) >= 1 else []


RATE_RE = re.compile(r"((?:\d+\s*개월\s*(?:단위|마다)\s*)?(?:연\s*)?(?:복리|단리)?\s*(?:연\s*)?\d+(?:\.\d+)?\s*%"
                     r"(?:\s*\((?:연)?\s*(?:복리|단리)\))?(?:\s*\(\s*\d+\s*개월\s*단위\s*\))?)")


def rate_text(sec):
    """'3개월 단위 연복리 2.0%' · '연 2.0%(연복리)' 같은 이율 문구 (복리·단리·'연 x%' 표시가 있는 첫 문구)"""
    for m in RATE_RE.finditer(sec):
        t = m.group(1).strip()
        if re.search(r"복리|단리|연\s*\d", t):
            return t
    return None


def parse_rate(t):
    """이율 문구 → (명목 연이율, 복리 방식, 복리 단위 개월, 메모).
    복리 방식은 Temp 목록(분복리·연복리·단리·고정율)으로 맞춘다: 3개월 단위 복리 → '분복리'."""
    y = num(re.sub(r"\d+\s*개월", "", t)) / 100
    comp = "단리" if "단리" in t else "연복리"
    mm = re.search(r"(\d+)\s*개월", t)
    months = int(mm.group(1)) if mm else 12
    note = None
    if comp == "연복리" and months == 3:
        comp = "분복리"
        note = f"계약은 3개월 단위 복리 {y:.2%} → Temp '분복리'. 상환율 표가 있으면 표 값을 그대로 씁니다."
    elif comp == "연복리" and months != 12:
        eff = (1 + y * months / 12) ** (12 / months) - 1
        note = f"계약은 {months}개월 단위 복리 {y:.2%} (연 환산 {eff:.4%}) — Temp 목록에 없어 연복리로 두었습니다. 상환율 표가 있으면 표 값을 씁니다."
    if "복리" not in t and "단리" not in t:
        note = "복리·단리 표시 없음 → 연복리(기본값)"
    return y, comp, months, note


def parse_cb(raw, kind="CB"):
    """발행결정 API 값 + 원문 파싱 → Temp DW 입력 필드 {키: {value, evidence, note, conf}}"""
    rc = raw["rcept_no"]
    f = {}
    lab_price, lab_period = LABEL[kind]

    def put(key, value, evid=None, note=None, conf="high"):
        f[key] = {"value": value, "evidence": [evid] if evid else [], "note": note, "conf": conf}

    put("회차", (raw.get("bd_tm") or "").strip(), ev(rc, "회차", (raw.get("bd_tm") or "").strip()))
    put("발행총액", num(raw.get("bd_fta")), ev(rc, "권면", raw.get("bd_fta") or ""))
    put("잔여총액", num(raw.get("bd_fta")), ev(rc, "권면", raw.get("bd_fta") or ""),
        note="발행 당시 금액입니다. 이후 전환·상환·취득 공시를 확인해 평가기준일 잔액으로 바꿉니다.", conf="mid")
    put("표면이자율", (num(raw.get("bd_intr_ex")) or 0) / 100, ev(rc, "표면이자율", raw.get("bd_intr_ex") or ""))
    put("만기이자율", (num(raw.get("bd_intr_sf")) or 0) / 100, ev(rc, "만기이자율", raw.get("bd_intr_sf") or ""))
    put("만기일", kdate(raw.get("bd_mtd")), ev(rc, "사채만기일", raw.get("bd_mtd") or ""))
    put("발행일", kdate(raw.get("pymd")) or kdate(raw.get("sbd")), ev(rc, "납입일", raw.get("pymd") or ""))
    pk = PRICE_KEY[kind]
    put("최초전환가격", num(raw.get(pk)), ev(rc, lab_price, raw.get(pk) or ""))
    put("현조정행사가격", num(raw.get(pk)), ev(rc, lab_price, raw.get(pk) or ""),
        note="발행 당시 가액입니다. 이후 조정 공시가 있으면 그 값으로 바꿉니다.", conf="mid")
    sk, ek = PERIOD_KEYS[kind]
    put("전환초일", kdate(raw.get(sk)), ev(rc, "시작일", raw.get(sk) or ""))
    put("전환말일", kdate(raw.get(ek)), ev(rc, "종료일", raw.get(ek) or ""))
    put("사모여부", "사모" if "사모" in (raw.get("bdis_mthn") or "") else "공모", ev(rc, "사채발행방법", raw.get("bdis_mthn") or ""))
    nk, rk = SHARES_KEY[kind]
    put("전환주식수", num(raw.get(nk)), ev(rc, "주식수", raw.get(nk) or ""), conf="high")
    put("주식총수대비", (num(raw.get(rk)) or 0) / 100, ev(rc, "주식총수", raw.get(rk) or ""), conf="high")
    put("연간이자지급횟수", 1, None, note="표면이자가 없거나 지급 주기 표시가 없으면 1 (Temp 기준)", conf="mid")

    try:
        p = plain(rc)
    except Exception as ex:
        f["_doc_error"] = {"value": str(ex)}
        return f

    if (f["표면이자율"]["value"] or 0) > 0:
        m = re.search(r"매\s*(\d+)\s*개월\s*마다[^.]{0,40}이자", p) or re.search(r"이자[^.]{0,60}?매\s*(\d+)\s*개월", p)
        if m:
            put("연간이자지급횟수", round(12 / int(m.group(1)), 4), ev(rc, "이자지급방법", m.group(0)[:60]))

    m = re.search(r"만기일에[^.]{0,80}?(\d{2,3}\.\d+)\s*%", p) or re.search(r"원금상환방법[^.]{0,160}?(\d{2,3}\.\d+)\s*%", p)
    if m:
        ratio = float(m.group(1)) / 100
        put("상환활증방식", "고정율", ev(rc, "원금상환방법", m.group(1) + "%"),
            note=f"계약상 만기 상환율 {m.group(1)}%를 그대로 씁니다 (Temp AL=고정율, AM=상환율).")
        put("RedemR", ratio, ev(rc, "원금상환방법", m.group(1) + "%"))
    else:
        put("상환활증방식", "연복리", None, note="만기 상환율 표시 없음 → 연복리(기본값)", conf="mid")
        put("RedemR", None, None)

    puts, calls = sections(p)

    ptab = []
    for sec in sorted(puts, key=len, reverse=True):
        ptab = exercise_table(sec)
        if ptab:
            break
    m = None
    for sec in puts:
        m = re.search(rf"{DATE}[^.]{{0,40}}?이후\s*매\s*(\d+)\s*개월", sec)
        if m:
            break
    if ptab:
        lab = "조기상환율"
        put("Put초일", ptab[0][0], ev(rc, lab, ptab[0][2]))
        put("Put말일", ptab[-1][0], ev(rc, lab, ptab[-1][2]))
        put("Put일정", [[d, r] for d, r, _ in ptab], ev(rc, lab, ptab[0][2]),
            note=f"공시의 조기상환 일정표 {len(ptab)}회 (지급일·상환율)를 그대로 씁니다.")
        gap = months_between(ptab[0][0], ptab[1][0]) if len(ptab) > 1 else None
        if m:
            put("Put연회수", round(12 / int(m.group(2)), 4), ev(rc, "Put Option", m.group(0)[:80]))
        elif gap:
            put("Put연회수", round(12 / round(gap), 4), None, note=f"일정표 간격 {round(gap)}개월", conf="mid")
    elif m:
        put("Put초일", kdate(m.group(1)), ev(rc, "Put Option", m.group(0)[:80]))
        put("Put연회수", round(12 / int(m.group(2)), 4), ev(rc, "Put Option", m.group(0)[:80]))
        last, d = None, f["Put초일"]["value"]
        step = int(m.group(2))
        while d and f["만기일"]["value"] and d < f["만기일"]["value"]:
            last, d = d, add_months(d, step)
        put("Put말일", last, None, note="Put 초일부터 주기대로 셌을 때 만기 전 마지막 날(자동 계산). 원문과 대조하세요.", conf="mid")
    elif puts:
        put("Put초일", None, None, note="조기상환 조항은 있는데 시작일을 찾지 못했습니다. 원문을 확인하세요.", conf="low")
    if "Put연회수" not in f and puts:
        put("Put연회수", 12, None, note="주기 표시 없음 → 12 (Temp 기준)", conf="low")

    t = None
    for sec in sorted(puts, key=len, reverse=True):
        t = rate_text(re.sub(r"\d+\s*차\s+.*?%", " ", sec))
        if t:
            break
    if t:
        y, comp, months, note = parse_rate(t)
        put("YTP", y, ev(rc, "조기상환", t), note=note)
        put("YTP복리", comp, ev(rc, "조기상환", t), note=note)
    elif puts:
        mi = f["만기이자율"]["value"]
        note = "조기상환수익률 문구가 없어 만기이자율(명목)과 같다고 두었습니다."
        if ptab and f["발행일"]["value"]:
            tau = months_between(f["발행일"]["value"], ptab[-1][0]) / 12
            if tau > 0:
                note += f" 일정표 마지막 상환율 {ptab[-1][1]:.4%}에서 역산한 연복리 수익률은 {ptab[-1][1] ** (1 / tau) - 1:.4%}입니다."
        put("YTP", mi, None, note=note, conf="mid" if ptab else "low")
        put("YTP복리", "연복리", None, note="복리·단리 표시 없음 → 연복리(기본값)", conf="mid" if ptab else "low")

    if calls:
        cs = max(calls, key=len)
        cwins = [p[mm_.start():mm_.start() + 2500] for mm_ in re.finditer(r"Call Option|매도청구권에\s*관한", p)]
        ctab = []
        for sec in sorted(calls, key=len, reverse=True):
            ctab = exercise_table(sec)
            if ctab:
                break
        m = re.search(DATE, cs)
        mm = None
        for sec in sorted(calls, key=len, reverse=True) + cwins:
            mm = re.search(rf"{DATE}\s*(?:부터|~)\s*{DATE}\s*까지", sec)
            if mm:
                break
        if ctab:
            put("Call초일", ctab[0][0], ev(rc, "매도청구권", ctab[0][2]))
            put("Call말일", ctab[-1][0], ev(rc, "매도청구권", ctab[-1][2]))
            put("Call일정", [[d, r] for d, r, _ in ctab], ev(rc, "매도청구권", ctab[0][2]),
                note=f"공시의 매도청구 일정표 {len(ctab)}회 (매매일·상환율)를 그대로 씁니다.")
        elif mm:
            put("Call초일", kdate(mm.group(1)), ev(rc, "Call Option", mm.group(1)))
            put("Call말일", kdate(mm.group(2)), ev(rc, "Call Option", mm.group(2)))
        elif m:
            put("Call초일", kdate(m.group(1)), ev(rc, "Call Option", m.group(1)))
            put("Call말일", kdate(m.group(1)), ev(rc, "Call Option", m.group(1)), note="행사일이 하루로 보입니다.", conf="mid")
        mf = re.search(r"매\s*(\d+)\s*개월", cs)
        if mf:
            put("Call연회수", round(12 / int(mf.group(1)), 4), ev(rc, "Call Option", mf.group(0)))
        elif len(ctab) > 1:
            put("Call연회수", round(12 / round(months_between(ctab[0][0], ctab[1][0])), 4), None,
                note=f"일정표 간격 {round(months_between(ctab[0][0], ctab[1][0]))}개월로 계산", conf="mid")
        else:
            put("Call연회수", 12, None, note="주기 표시 없음 → 12 (Temp 기준)", conf="low")
        t = rate_text(re.sub(r"\d+\s*차\s+.*?%", " ", cs))
        if t:
            y, comp, months, note = parse_rate(t)
            put("YTC", y, ev(rc, "매매가액", t), note=note)
            put("YTC복리", comp, ev(rc, "매매가액", t), note=note)
        else:
            put("YTC", 0, None, note="매도청구 이율 문구가 없습니다 (내용 없으면 0).", conf="low")
            put("YTC복리", "연복리", None, conf="low")
        lim = None
        for sec in calls + cwins:
            m2 = (re.search(r"\(\s*Call\s*Option\s*(\d+(?:\.\d+)?)\s*%\s*\)", sec)
                  or re.search(r"(?:권면|전자등록)\s*(?:총액|금액)의\s*(\d+(?:\.\d+)?)\s*%\s*(?:를|을)?\s*(?:초과|한도)", sec)
                  or re.search(r"(?:본\s*사채|사채)의\s*(\d+(?:\.\d+)?)\s*%\s*(?:를|을)\s*초과하여", sec)
                  or re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:를|을)?\s*(?:초과하여|한도로|범위\s*내)", sec))
            if m2:
                lim = m2
                break
        if lim:
            put("Call한도", float(lim.group(1)) / 100, ev(rc, "Call Option", lim.group(0)))
        else:
            put("Call한도", 1.0, None, note="한도 비율 표시 없음 → 100% (Temp 기준)", conf="low")
        third = re.search(r"발행회사가\s*지정하는\s*(?:자|제3자)", cs)
        if third:
            put("Call3자배정", "Y", ev(rc, "매도청구권", third.group(0)))
        else:
            put("Call3자배정", "N", None, note='"발행회사가 지정하는 자" 문구 없음 → N (추정)', conf="mid")
        sub = re.search(r"[^.。\n]{0,40}후순위[^.。\n]{0,20}", cs)
        if sub:
            put("Call순위", "후순위", ev(rc, "매도청구권", sub.group(0).strip()))
        else:
            put("Call순위", "선순위", None, note='"후순위" 표현 없음 → 선순위 (Temp 기준, 추정)', conf="mid")
    else:
        put("Call초일", None, None, note="매도청구권(Call) 조항을 찾지 못했습니다 → Call 없음으로 둡니다. 원문을 확인하세요.", conf="mid")
        put("Call한도", 0, None, note="Call 조항 없음 → 0", conf="mid")

    floor_api = raw.get("act_mktprcfl_cvprc_lwtrsprc")
    floor_doc = re.search(r"최저\s*조정\s*가액\s*\(원\)\s*([\d,]{2,})", p)
    none_doc = re.search(r"시가\s*하락에\s*따른\s*(?:전환|행사|교환)?\s*(?:가격|가액)?\s*조정\s*:?\s*없음", p)
    refix_m = re.search(r"매\s*(\d+)\s*개월\s*(?:마다|이\s*되는\s*날|에\s*해당하는\s*날)[^.]{0,60}?(?:전환|행사|교환)\s*(?:가격|가액)\s*조정일"
                        r"|매\s*(\d+)\s*개월\s*(?:경과일|이\s*경과한\s*날)?[^.]{0,90}?마다[^.]{0,30}?(?:전환|행사|교환)\s*가(?:격|액)?(?:을|를)?\s*조정"
                        r"|(?:전환|행사|교환)\s*가(?:격|액)?\s*[\"“”']?\s*조정일\s*[\"“”']?\s*전일을\s*기산일", p)
    has_floor = bool((floor_api and num(floor_api)) or floor_doc)
    if (has_floor or refix_m) and not none_doc:
        put("상하향조정", "하", ev(rc, "전환가액 조정", refix_m.group(0)[:40]) if refix_m else None)
        seg = p[refix_m.start():refix_m.start() + 2500] if refix_m else ""
        up = seg and re.search(
            r"(?:높은|상회하는)\s*경우에는?\s*[^.]{0,40}?(?:을|를)\s*새로운\s*(?:전환|행사|교환)\s*가(?:액|격)"
            r"|(?:전환|행사|교환)가(?:액|격)을\s*상향\s*조정하는\s*경우"
            r"|(?:높은|상회하는)\s*경우[^.]{0,80}상향"
            r"|조정\s*후\s*(?:전환|행사|교환)\s*가(?:액|격)은\s*최초\s*(?:전환|행사|교환)\s*가(?:액|격)", seg)
        if up:
            put("상하향조정", "상하", ev(rc, "전환가액 조정", up.group(0)[:60]),
                note="시가가 오르면 다시 올리는(상향) 조정 조항이 있습니다 — Temp 입력례 '상하' (최초 전환가까지).")
        mf = re.search(r"매\s*(\d+)\s*개월", refix_m.group(0)) if refix_m else None
        if not mf and refix_m:
            mf = re.search(r"매\s*(\d+)\s*개월", p[max(0, refix_m.start() - 300):refix_m.start()])
        if not mf and refix_m:
            mf = re.search(r"매\s*(\d+)\s*개월\s*(?:마다|이\s*되는\s*날|에\s*해당하는\s*날)", p[max(0, refix_m.start() - 200):refix_m.end() + 80])
        if mf:
            put("전환가조정연회수", round(12 / int(mf.group(1)), 4), ev(rc, "전환가격 조정일", mf.group(0)))
            if f["발행일"]["value"]:
                put("전환가격조정시작일", add_months(f["발행일"]["value"], int(mf.group(1))), None,
                    note=f"발행일 + {mf.group(1)}개월 (첫 조정일, 자동 계산)", conf="mid")
        mp = re.search(r"(?:최초|발행\s*당시(?:의)?)\s*(?:전환|행사|교환)\s*(?:가격|가액)\s*(?:\([^)]{0,200}\))?\s*의\s*"
                       r"(?:100분의\s*(\d+)|(\d+)\s*%)\s*(?:이상|에\s*해당하는\s*가액)", seg or p)
        fp = f["최초전환가격"]["value"] or 0
        if mp and fp:
            pct = int(mp.group(1) or mp.group(2))
            note = f"최초 전환가 {fp:,.0f} × {pct}% (Temp 입력례: '최초전환가*70%'는 수식)"
            if has_floor:
                note += f" / 공시 표의 최저 조정가액 {num(floor_api) or num(floor_doc.group(1)):,.0f}원"
            put("Refix최소", round(fp * pct / 100, 4), ev(rc, "최저 조정한도", mp.group(0)), note=note)
        elif has_floor:
            v = num(floor_api) if floor_api and num(floor_api) else num(floor_doc.group(1))
            put("Refix최소", v, ev(rc, "최저 조정가액", floor_doc.group(1) if floor_doc else floor_api))
        else:
            put("Refix최소", None, None, note="리픽싱 하한을 찾지 못했습니다. 원문을 확인하세요.", conf="low")
    else:
        put("상하향조정", "일반", None, note="시가하락 리픽싱 조항이 없어 '일반' (Temp 실무 입력례와 같음)", conf="mid")
        put("전환가조정연회수", 12, None, note="리픽싱 없음 → 12 (Temp 기준)", conf="mid")
    if "전환가조정연회수" not in f:
        put("전환가조정연회수", 12, None, note="주기 표시 없음 → 12 (Temp 기준)", conf="low")
    return f


def disclosures(corp_code, years=6):
    end = dt.date.today()
    bgn = end - dt.timedelta(days=365 * years)
    out, page = [], 1
    while page <= 8:
        j = _json("list.json", corp_code=corp_code, bgn_de=bgn.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                  page_no=page, page_count=100)
        out += j.get("list", []) or []
        if page >= int(j.get("total_page", 1) or 1):
            break
        page += 1
    return out


def _iso(d8):
    return f"{d8[:4]}-{d8[4:6]}-{d8[6:8]}"


def _tm_re(bd_tm):
    return rf"(?<![\d,.])0*{re.escape(str(bd_tm))}\s*(?:회차|회)?"


def history(corp_code, bd_tm, kind="CB", items=None, val_date=None):
    """회차별 전환가액 조정·잔액 변동 이력 (최신순). 각 항목에 근거를 붙인다."""
    items = items if items is not None else disclosures(corp_code)
    word = {"CB": "전환", "BW": "신주인수권|행사", "EB": "교환"}[kind]
    out = []
    for d in items:
        nm = d["report_nm"]
        if not re.search(r"전환가액|행사가액|교환가액|전환청구권행사|신주인수권행사|교환청구권행사|만기전사채취득|사채취득", nm):
            continue
        if not re.search(word, nm) and "사채취득" not in nm:
            continue
        try:
            p = plain(d["rcept_no"])
        except Exception:
            continue
        rc, date = d["rcept_no"], _iso(d["rcept_dt"])
        if re.search(r"가액의?\s*조정", nm):
            m = re.search(r"조정\s*후\s*(?:전환|행사|교환)\s*가액\s*\(원\)\s*((?:\d+\s+(?:비상장|상장|-)?\s*[\d,]+\s+[\d,]+\s*)+)", p)
            rows = re.findall(r"(\d+)\s+(?:비상장|상장|-)?\s*([\d,]+)\s+([\d,]+)", m.group(1)) if m else []
            row = next((r for r in rows if r[0] == str(bd_tm)), None)
            if not row:
                continue
            ap = re.search(rf"조정가액\s*적용일\s*{DATE}", p)
            fl = re.search(r"최저\s*조정\s*한도\s*:?\s*([\d,]+)\s*원", p)
            bal = re.search(rf"미전환사채의\s*권면\s*\(전자등록\)\s*총액.*?주식수\s*\(주\)\s*{row[0]}\s+([\d,]+)\s*KRW[^0-9]*?([\d,]+)\s+([\d,]+)", p) \
                or re.search(rf"미전환사채의\s*권면\s*\(전자등록\)\s*총액.*?주식수\s*\(주\)\s*{row[0]}\s+([\d,]+)\s*KRW", p)
            out.append({"type": "refix", "date": kdate(ap.group(1)) if ap else date, "rcept_no": rc, "report_nm": nm.strip(),
                        "before": num(row[1]), "after": num(row[2]),
                        "floor": num(fl.group(1)) if fl else None, "balance": num(bal.group(1)) if bal else None,
                        "shares_after": num(bal.group(3)) if bal and bal.lastindex and bal.lastindex >= 3 else None,
                        "ev_price": ev(rc, "조정후", row[2]), "ev_floor": ev(rc, "최저 조정 한도", fl.group(1)) if fl else None,
                        "ev_balance": ev(rc, "미전환사채의 권면", bal.group(1)) if bal else None})
        elif re.search(r"청구권행사|신주인수권행사", nm):
            k = p.find("사채 잔액 회차")
            seg = p[k:] if k >= 0 else (p[p.find("잔액"):] if "잔액" in p else p)
            rows = re.findall(r"(\d+)\s+([\d,]{5,})\s*KRW[^0-9]{0,40}?([\d,]{1,}|-)\s*KRW", seg)
            row = next((r for r in rows if r[0] == str(bd_tm)), None)
            if not row:
                continue
            out.append({"type": "conversion", "date": date, "rcept_no": rc, "report_nm": nm.strip(),
                        "balance": num(row[2]) or 0.0, "ev_balance": ev(rc, "미전환사채 잔액", row[2])})
        elif "사채취득" in nm:
            if not re.search(rf"\s{re.escape(str(bd_tm))}\s*회차", p[:600]):
                continue
            got = re.search(r"취득한\s*사채의\s*권면\s*\(전자등록\)\s*총액\s*\(통화단위\)\s*([\d,]+)", p)
            aft = re.search(r"취득\s*후\s*사채의\s*권면\s*\(전자등록\)\s*총액\s*\(통화단위\)\s*([\d,]+|-)", p)
            when = re.search(rf"취득일자\s*{DATE}", p)
            why = re.search(r"취득사유\s*:\s*(.+?)\s*-\s*(?:향후\s*)?처리방법\s*:\s*(.+?)\s*\d\.\s", p)
            out.append({"type": "buyback", "date": kdate(when.group(1)) if when else date, "rcept_no": rc, "report_nm": nm.strip(),
                        "amount": num(got.group(1)) if got else None,
                        "balance": (num(aft.group(1)) or 0.0) if aft else None,
                        "reason": why.group(1).strip() if why else None, "after_use": why.group(2).strip() if why else None,
                        "ev_balance": ev(rc, "취득후 사채의 권면", aft.group(1)) if aft else None})
    out.sort(key=lambda x: (x["date"] or "", x["rcept_no"]), reverse=True)
    return out


def apply_history(fields, hist, val_date):
    """평가기준일 이전 최신 이력으로 현조정행사가격·잔여총액·리픽싱 하한을 바꾼다."""
    vd = val_date or dt.date.today().isoformat()
    past = [h for h in hist if (h["date"] or "") <= vd]
    rf = next((h for h in past if h["type"] == "refix"), None)
    if rf:
        fields["현조정행사가격"] = {"value": rf["after"], "evidence": [rf["ev_price"]], "conf": "high",
                              "note": f"{rf['date']} 적용 '{rf['report_nm']}' 공시의 조정 후 가액 (조정 전 {rf['before']:,.0f})"}
        if rf.get("floor"):
            cur = fields.get("Refix최소") or {}
            if cur.get("value"):
                cur["note"] = (cur.get("note") or "") + f" / 조정 공시의 최저 조정 한도: {rf['floor']:,.0f}원 (원 단위 절상)"
                cur.setdefault("evidence", []).append(rf["ev_floor"])
            else:
                fields["Refix최소"] = {"value": rf["floor"], "evidence": [rf["ev_floor"]], "conf": "high",
                                     "note": "조정 공시에 적힌 최저 조정 한도"}
        if fields.get("상하향조정", {}).get("value") == "일반":
            fields["상하향조정"] = {"value": "하", "evidence": [], "conf": "mid", "note": "리픽싱 조정 공시가 있어 '하'"}
    bal = next((h for h in past if h.get("balance") is not None), None)
    if bal:
        what = {"refix": "조정 공시의 미전환 잔액", "conversion": "전환청구 공시의 신고일 현재 미전환 잔액",
                "buyback": "만기전 취득 공시의 취득 후 권면총액"}[bal["type"]]
        note = f"{bal['date']} {what}. 이후 재매각(자기사채 매도)·추가 전환이 있으면 달라집니다."
        if bal.get("shares_after") and bal.get("after"):
            note += (f" 참고: 조정 후 전환가능주식수 {bal['shares_after']:,.0f}주 × {bal['after']:,.0f}원 = "
                     f"{bal['shares_after'] * bal['after']:,.0f}원 (주식수 기준으로 입력하는 경우)")
        fields["잔여총액"] = {"value": bal["balance"], "evidence": [bal["ev_balance"]] if bal.get("ev_balance") else [],
                          "conf": "high" if bal["type"] != "buyback" else "mid", "note": note}
    return fields


def corporate_actions(corp_code, items=None):
    """주식 수가 바뀌는 사건. price_factor = 같은 회사 가치에서 주가가 몇 배가 되는지 (병합 5:1 → 5, 분할 1:5 → 0.2).
    같은 이사회결의일의 정정 공시는 최신 것만 쓴다 (목록이 최신순)."""
    items = items if items is not None else disclosures(corp_code)
    acts = {}
    for d in items:
        m = re.search(r"주식병합결정|주식분할결정|무상증자결정|감자결정", d["report_nm"])
        if not m:
            continue
        try:
            p = plain(d["rcept_no"])
        except Exception:
            continue
        kind = m.group(0).replace("결정", "")
        board = re.search(rf"이사회\s*결의일\s*(?:\(결정일\))?\s*{DATE}", p)
        key = (kind, kdate(board.group(1)) if board else d["rcept_dt"])
        if key in acts:
            continue
        rc = d["rcept_no"]

        def last(pattern):
            ms = list(re.finditer(pattern, p))
            return ms[-1] if ms else None
        eff = last(rf"신주의\s*효력\s*발생일\s*{DATE}") or last(rf"(?:감자|병합|분할)\s*기준일\s*{DATE}")
        lst = last(rf"신주권?\s*상장\s*예정일\s*{DATE}")
        factor, evid = None, None
        if kind in ("주식병합", "주식분할", "감자"):
            r = re.search(r"(?:병합|분할|감자)\s*비율은?\s*(\d+(?:\.\d+)?)\s*[:：]\s*(\d+(?:\.\d+)?)", p)
            sh = re.search(r"발행주식\s*총수\s*보통주식?\s*\(주\)\s*([\d,]+)\s+([\d,]+)", p) \
                or re.search(r"(?:감자\s*전|병합\s*전)[^0-9]{0,40}([\d,]{4,})[^0-9]{1,40}?([\d,]{4,})", p)
            if r:
                factor, evid = float(r.group(1)) / float(r.group(2)), ev(rc, "비율", r.group(0))
            elif sh and num(sh.group(2)):
                factor, evid = num(sh.group(1)) / num(sh.group(2)), ev(rc, "발행주식", sh.group(1))
        elif kind == "무상증자":
            n = re.search(r"1주당\s*신주\s*배정\s*주식수\s*\(?주\)?\s*보통주식?\s*\(?주?\)?\s*([\d.]+)", p)
            base = re.search(rf"신주\s*배정\s*기준일\s*{DATE}", p)
            if n and float(n.group(1)) > 0:
                factor, evid = 1 / (1 + float(n.group(1))), ev(rc, "1주당 신주배정", n.group(1))
                eff = eff or base
                lst = lst or base
        if not factor or abs(factor - 1) < 1e-9:
            continue
        acts[key] = {"kind": kind, "board": key[1], "effective": kdate(eff.group(1)) if eff else None,
                     "listing": kdate(lst.group(1)) if lst else None, "factor": factor, "rcept_no": rc,
                     "report_nm": d["report_nm"].strip(), "evidence": evid}
    return sorted(acts.values(), key=lambda a: a["effective"] or a["listing"] or a["board"] or "")


def _fmt_ratio(f):
    return f"{f:g}:1" if f >= 1 else f"1:{1 / f:g}"


def adjust_for_actions(fields, acts, decision_date, val_date, hist):
    """발행결정 이후 ~ 평가기준일 사이에 효력이 난 병합·분할만큼 최초전환가·하한(·현 전환가)을 맞춘다.
    (전환가 조정 조항 '합병, 자본의 감소, 주식분할 및 병합 … 그 비율을 고려하여 조정')"""
    vd = val_date or dt.date.today().isoformat()
    dec = decision_date or "0000-00-00"
    applied = [a for a in acts if (a["effective"] or "") and dec <= a["effective"] <= vd]
    scale = 1.0
    for a in applied:
        scale *= a["factor"]
    if abs(scale - 1) < 1e-9:
        return []
    why = ", ".join(f"{a['kind']} {_fmt_ratio(a['factor'])} ({a['effective']} 효력)" for a in applied)
    for k in ("최초전환가격",):
        f = fields.get(k) or {}
        if f.get("value"):
            old = f["value"]
            f["value"] = round(old * scale, 4)
            f["note"] = (f.get("note") or "") + f" 공시 {old:,.0f}원 × {scale:g} ({why})"
            f.setdefault("evidence", []).extend([a["evidence"] for a in applied if a.get("evidence")])
    fl = fields.get("Refix최소") or {}
    if fl.get("value"):
        old = fl["value"]
        fl["value"] = round(old * scale, 4)
        fl["note"] = (fl.get("note") or "") + f" → 병합·분할 반영 {old:,.1f} × {scale:g} = {fl['value']:,.1f}원 ({why})"
    last_refix = next((h for h in hist if h["type"] == "refix" and (h["date"] or "") <= vd), None)
    cur = fields.get("현조정행사가격") or {}
    last_eff = max(a["effective"] for a in applied)
    if cur.get("value") and (not last_refix or (last_refix["date"] or "") < last_eff):
        old = cur["value"]
        cur["value"] = round(old * scale, 4)
        cur["note"] = (cur.get("note") or "") + f" → 병합·분할 반영 {old:,.0f} × {scale:g} ({why})"
    return applied


def price_divisor_after(acts, val_date, today=None):
    """평가기준일 뒤에 상장(권리락)된 병합·분할 → 야후·RAS 주가는 소급조정돼 있으므로 나눌 배율."""
    today = today or dt.date.today().isoformat()
    after = [a for a in acts if val_date < (a["listing"] or a["effective"] or "9999") <= today]
    div = 1.0
    for a in after:
        div *= a["factor"]
    return div, after


def fin_audit(corp_code, val_date=None):
    vd = dt.date.fromisoformat(val_date) if val_date else dt.date.today()
    out = {}
    y0 = min(vd.year, dt.date.today().year)
    for y in (y0, y0 - 1, y0 - 2):
        try:
            j = _json("fnlttSinglAcnt.json", corp_code=corp_code, bsns_year=str(y), reprt_code="11011")
        except Exception:
            continue
        rows = j.get("list") or []
        if not rows:
            continue
        div = "CFS" if any(r.get("fs_div") == "CFS" for r in rows) else "OFS"
        acc = {}
        for r in rows:
            if r.get("fs_div") == div and r["account_nm"] not in acc:
                acc[r["account_nm"]] = (num(r.get("thstrm_amount")), num(r.get("frmtrm_amount")), num(r.get("bfefrmtrm_amount")))
        out = {"year": y, "div": "연결" if div == "CFS" else "별도", "acc": acc, "rcept_no": rows[0].get("rcept_no")}
        try:
            a = _json("accnutAdtorNmNdAdtOpinion.json", corp_code=corp_code, bsns_year=str(y), reprt_code="11011")
            first = next((r for r in (a.get("list") or []) if r.get("adt_opinion")), None)
            if first:
                out["audit"] = {k: (first.get(k) or "").replace("\n", " ").strip()
                                for k in ("adtor", "adt_opinion", "emphs_matter", "core_adt_matter", "adt_reprt_spcmnt_matter")}
        except Exception:
            pass
        break
    return out


RULES = [
    (r"관리종목|상장적격성|상장폐지|투자주의환기", "bad", "관리종목·상장적격성", "주가·변동성의 신뢰성과 신용등급(할인율) 하향 필요성을 검토하세요."),
    (r"소송|횡령|배임|회생|파산", "bad", "소송·횡령·배임·회생", "우발부채와 신용위험(할인율), 기한이익상실 조항을 검토하세요."),
    (r"불성실공시", "warn", "불성실공시법인 지정", "공시 신뢰성과 지배구조 위험을 확인하세요."),
    (r"거래정지", "warn", "매매거래정지", "정지 사유·기간을 확인하세요. 평가기준일 주가·변동성 산정 구간에 걸리면 영향이 있어요."),
    (r"가액의?\s*조정", "warn", "전환·행사가액 조정(리픽싱)", "현조정행사가격(Temp AN열)이 평가기준일 직전 조정값인지 확인하세요."),
    (r"만기전\s*사채\s*취득|사채취득", "warn", "만기 전 사채 취득", "Put·Call 행사로 잔여총액(Temp AB열)이 줄었는지, 소각인지 재매각 예정인지 확인하세요."),
    (r"자기\s*(?:전환|교환)?사채\s*매도|자기전환사채", "warn", "자기 사채 재매각", "취득했던 사채를 다시 팔면 잔여총액이 늘어납니다. 매도 후 잔액을 확인하세요."),
    (r"청구권\s*행사|신주인수권\s*행사|추가상장", "info", "전환·행사 청구", "잔여총액이 줄었을 수 있습니다. 평가기준일 현재 미전환 잔액을 확인하세요."),
    (r"최대주주\s*변경", "warn", "최대주주 변경", "콜옵션 3자 지정 대상, 경영권 변동, 주가 급변 여부를 확인하세요."),
    (r"유상증자|무상증자|주식배당|감자|합병|분할|주식\s*병합", "warn", "자본 변동(증자·감자·합병 등)", "전환가액 조정 사유인지, 희석 효과가 반영됐는지 확인하세요."),
    (r"(?:전환|교환|신주인수권부)사채권\s*발행결정", "info", "CB·BW·EB 발행결정 (여러 회차)", "회차별로 조건이 다릅니다. 평가 대상 회차를 맞게 골랐는지 확인하세요."),
    (r"손익구조|매출액또는손익", "info", "매출·손익구조 30% 이상 변동", "영업손실 전환·확대 여부를 보고 신용등급 판단에 반영하세요."),
    (r"감사보고서", "info", "감사보고서 제출", "감사의견·계속기업 불확실성 문단을 확인하세요 (아래 재무·감사 요약)."),
    (r"조회공시", "info", "조회공시 요구", "주가 급등락 사유를 확인하세요. 평가기준일 주가의 대표성 문제예요."),
    (r"대표이사\s*변경", "info", "대표이사 변경", "지배구조 변동이 주가·신용에 미친 영향을 참고하세요."),
]


def alerts(corp_code, fields=None, items=None, val_date=None, hist=None, fa=None, kind="CB"):
    items = items if items is not None else disclosures(corp_code)
    vd = val_date or dt.date.today().isoformat()
    f = {k: (v or {}).get("value") for k, v in (fields or {}).items()}
    out = []

    def add(lvl, title, why, its=None, cat="공시"):
        out.append({"level": lvl, "title": title, "why": why, "items": its or [], "cat": cat})

    if f.get("잔여총액") is not None and f.get("잔여총액") == 0:
        add("bad", "평가기준일 현재 잔액 0", "이 회차는 평가기준일 전에 전액 상환·취득·전환됐습니다. 평가 대상인지 다시 확인하세요.", cat="회차")
    for h in (hist or []):
        it = [{"rcept_no": h["rcept_no"], "date": h["date"], "report_nm": h["report_nm"]}]
        after = (h["date"] or "") > vd
        tag = " (평가기준일 이후 · 후속사건)" if after else ""
        if h["type"] == "buyback":
            reason = h.get("reason") or "사유 미기재"
            add("warn" if not after else "info", f"{h['date']} 만기전 취득 {h['amount'] or 0:,.0f}원{tag}",
                f"사유: {reason} / 처리: {h.get('after_use') or '-'} / 취득 후 잔액 {h['balance'] or 0:,.0f}원", it, "회차")
        elif h["type"] == "refix":
            add("info", f"{h['date']} 가액 조정 {h['before']:,.0f} → {h['after']:,.0f}원{tag}",
                "조정 후 가액이 현조정행사가격에 반영됐는지 확인하세요." + (f" 최저 한도 {h['floor']:,.0f}원." if h.get("floor") else ""), it, "회차")
        elif h["type"] == "conversion":
            add("info", f"{h['date']} 전환청구 → 미전환 잔액 {h['balance']:,.0f}원{tag}", "잔여총액에 반영됐는지 확인하세요.", it, "회차")

    for pat, lvl, title, why in RULES:
        hits = [d for d in items if re.search(pat, d["report_nm"])]
        if hits:
            add(lvl, f"{title} ({len(hits)}건)", why,
                [{"rcept_no": d["rcept_no"], "date": _iso(d["rcept_dt"]), "report_nm": d["report_nm"].strip()} for d in hits[:8]])

    if f.get("사모여부") == "사모":
        add("info", "사모 발행", "할인율은 사모 시가평가기준수익률(사모부 등급)이 원칙이에요. Temp DU열 등급 표기를 확인하세요 (KICPA 가이드 3.7).", cat="조건")
    if f.get("상하향조정") in ("하", "상향", "상하"):
        cur, flo = f.get("현조정행사가격"), f.get("Refix최소")
        kind_txt = "하향만" if f.get("상하향조정") == "하" else "하향·상향(최초 전환가까지)"
        if not flo:
            add("warn", "리픽싱 하한 없음", "하한이 비어 있으면 Temp처럼 리픽싱을 계산하지 않습니다 (Refix=N). 하한을 확인하세요 (KICPA 3.6).", cat="조건")
        elif cur and cur > flo + 1:
            add("warn", f"리픽싱 여력 있음 ({kind_txt})",
                f"현 전환가 {cur:,.0f}원 > 하한 {flo:,.0f}원. 계산에 Temp 경로확률 방식 리픽싱을 반영합니다. 조정 주기·첫 조정일을 확인하세요 (KICPA 3.6).", cat="조건")
        else:
            add("info", f"리픽싱 조항 ({kind_txt})",
                (f"현 전환가가 하한({flo:,.0f}원)이라 추가 하향 여지는 없습니다." + (" 주가가 오르면 다시 올라갈 수 있습니다." if f.get("상하향조정") != "하" else ""))
                + " (KICPA 3.6)", cat="조건")
    if f.get("Call초일"):
        if f.get("Call한도") and f["Call한도"] < 1:
            add("info", f"Call 한도 {f['Call한도']:.0%}", "Call 대상이 일부라 Call 가치는 한도 비율만큼만 반영해요. Temp BH열 확인.", cat="조건")
        if f.get("Call3자배정") == "Y":
            add("warn", "발행회사가 지정하는 제3자 Call", "제3자 지정 콜은 회계 분류(별도 파생자산)와 특수관계자 거래 여부를 함께 검토하세요.", cat="조건")
        if f.get("Call말일") and f["Call말일"] < vd:
            add("info", "Call 행사기간 종료", f"Call 말일 {f['Call말일']}이 평가기준일 전이라 Call 가치는 0입니다.", cat="조건")
    if f.get("Put말일") and f["Put말일"] < vd:
        add("info", "Put 행사기간 종료", f"Put 말일 {f['Put말일']}이 평가기준일 전입니다.", cat="조건")
    if (f.get("표면이자율") or 0) == 0 and (f.get("만기이자율") or 0) > 0 and not f.get("RedemR"):
        add("info", "표면 0% · 만기보장 이율 있음", "상환할증 방식(연복리/단리)을 확인하세요. 표시가 없으면 연복리가 기본이에요.", cat="조건")
    if f.get("전환초일") and vd < f["전환초일"]:
        add("info", "평가기준일이 전환청구기간 전", "전환 시작 전 구간이 반영됐는지 확인하세요.", cat="조건")
    if f.get("주식총수대비") and f["주식총수대비"] >= 0.1:
        add("info", f"전환 시 희석 {f['주식총수대비']:.1%}", "발행주식총수 대비 비율이 큽니다. 희석효과 검토 대상인지 보세요 (KICPA 3.4).", cat="조건")
    if kind == "EB":
        add("info", "교환사채(EB)", "교환 대상이 자기주식이면 신주 희석이 없습니다. 교환 대상 주식과 수량을 확인하세요.", cat="조건")
    if kind == "BW":
        add("warn", "신주인수권부사채(BW)", "대시보드 계산은 전환사채(비분리형) 방식입니다. 분리형이면 사채와 신주인수권을 따로 평가하세요.", cat="조건")

    if fa and fa.get("acc"):
        a = fa["acc"]
        g = lambda k, i=0: (a.get(k) or (None, None, None))[i]
        eq, cap, debt = g("자본총계"), g("자본금"), g("부채총계")
        op0, op1 = g("영업이익"), g("영업이익", 1)
        ni0 = g("당기순이익(손실)") if a.get("당기순이익(손실)") else g("당기순이익")
        lab = f"{fa['year']} 사업보고서({fa['div']})"
        if eq is not None and cap and eq < cap:
            add("bad", "자본잠식" + (" (완전)" if eq <= 0 else " (부분)"), f"{lab}: 자본총계 {eq/1e8:,.0f}억 < 자본금 {cap/1e8:,.0f}억. 신용등급·계속기업 검토.", cat="재무")
        if op0 is not None and op0 < 0 and op1 is not None and op1 < 0:
            add("warn", "2년 연속 영업손실", f"{lab}: 영업손실 {op0/1e8:,.1f}억 (전기 {op1/1e8:,.1f}억). 신용등급(Rd 곡선) 판단에 반영하세요.", cat="재무")
        elif op0 is not None and op0 < 0:
            add("info", "영업손실", f"{lab}: 영업손실 {op0/1e8:,.1f}억.", cat="재무")
        if eq and debt is not None and eq > 0 and debt / eq > 2:
            add("warn", f"부채비율 {debt/eq:.0%}", f"{lab}: 부채 {debt/1e8:,.0f}억 / 자본 {eq/1e8:,.0f}억.", cat="재무")
        au = fa.get("audit") or {}
        if au:
            op = au.get("adt_opinion", "")
            em = au.get("emphs_matter", "")
            if op and "적정" not in op:
                add("bad", f"감사의견: {op}", f"{fa['year']} 감사인 {au.get('adtor')}.", cat="감사")
            if re.search(r"계속기업", em + au.get("adt_reprt_spcmnt_matter", "")):
                add("bad", "계속기업 불확실성 강조사항", f"{fa['year']} 감사보고서 강조사항: {em[:120]}", cat="감사")
        summary = {"lab": lab, "rcept_no": fa.get("rcept_no"), "자산총계": g("자산총계"), "부채총계": debt, "자본총계": eq,
                   "자본금": cap, "매출액": g("매출액"), "영업이익": op0, "영업이익(전기)": op1, "당기순이익": ni0,
                   "감사인": au.get("adtor"), "감사의견": au.get("adt_opinion"), "강조사항": au.get("emphs_matter"),
                   "핵심감사사항": au.get("core_adt_matter")}
    else:
        summary = None
    order = {"bad": 0, "warn": 1, "info": 2}
    out.sort(key=lambda x: (order[x["level"]], {"회차": 0, "재무": 1, "감사": 1, "조건": 2, "공시": 3}.get(x["cat"], 4)))
    return out, summary
