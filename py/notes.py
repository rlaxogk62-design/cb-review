"""정기보고서 주석에서 종류주식(RCPS·CPS)·주식선택권(SO) 조건을 읽는다.

비상장이라도 사업·반기·분기보고서(또는 감사보고서)를 내면
  - '종류주식(명칭) 발행현황' 표준 양식 (칸마다 ACODE: ISS_DATE·ISS_PRC·TRS_PRD·STK_RPAY …)
  - 주석의 조건표 ('구분 | 5회 전환상환우선주 | 6회 …' 처럼 열이 회차인 표)
  - 주식기준보상 주석 (부여 조건표·평가 가정표·변동내역)
에서 평가 입력값을 근거와 함께 채운다.
"""
import calendar
import datetime as dt
import html
import re

from dart import DATE, _iso, add_months, disclosures, document_xml, ev, kdate, num, parse_rate, plain, rate_text

REPORT_RE = re.compile(r"(사업보고서|반기보고서|분기보고서|감사보고서)\s*\((\d{4})\.(\d{2})\)")
_tables_cache = {}


def _cell(body):
    t = body.replace("&cr;", " ")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def xml_tables(rcept_no):
    """원문 XML의 표 → [{pos, rows: [[{t, code, span}]]}]. 표준 양식 칸에는 ACODE·AUNIT 코드가 붙어 있다."""
    if rcept_no in _tables_cache:
        return _tables_cache[rcept_no]
    x = document_xml(rcept_no)
    out = []
    for m in re.finditer(r"<TABLE\b.*?</TABLE>", x, re.S | re.I):
        rows = []
        for tr in re.findall(r"<TR\b.*?</TR>", m.group(0), re.S | re.I):
            cells = []
            for tag, attrs, body in re.findall(r"<(TD|TE|TU|TH)\b([^>]*)>(.*?)</\1>", tr, re.S | re.I):
                code = re.search(r'A(?:CODE|UNIT)="([^"]+)"', attrs)
                span = re.search(r'COLSPAN="(\d+)"', attrs, re.I)
                cells.append({"t": _cell(body), "code": code.group(1) if code else None,
                              "span": int(span.group(1)) if span else 1})
            if cells:
                rows.append(cells)
        out.append({"pos": m.start(), "rows": rows})
    _tables_cache[rcept_no] = out
    return out


def periodic_reports(items):
    """공시 목록 → 정기보고서·감사보고서 [{rcept_no, report_nm, period(기준일), type}] (최신순)."""
    out = []
    for d in items:
        nm = re.sub(r"^\[[^\]]*\]\s*", "", d["report_nm"].strip())
        m = REPORT_RE.match(nm)
        if not m:
            continue
        y, mo = int(m.group(2)), int(m.group(3))
        out.append({"rcept_no": d["rcept_no"], "date": _iso(d["rcept_dt"]), "report_nm": nm, "type": m.group(1),
                    "period": dt.date(y, mo, calendar.monthrange(y, mo)[1]).isoformat()})
    return out


def pick_report(items, val_date=None):
    """평가기준일에 맞는 보고서: 기준일이 평가기준일 이전인 것 중 가장 최근 (같은 기준일이면 정기보고서 > 감사보고서)."""
    reps = periodic_reports(items)
    if not reps:
        return None
    vd = val_date or dt.date.today().isoformat()
    ok = [r for r in reps if r["period"] <= vd] or reps
    return max(ok, key=lambda r: (r["period"], r["type"] != "감사보고서", r["rcept_no"]))


def _transposed(tables):
    """'구분 | A | B …' 머리 행 + '항목 | 값 | 값 …' 행으로 된 주석 표 → [(열이름, {항목: 값}, 표 위치)].
    항목명 없이 값만 있는 행(병합 칸)은 앞 항목에 이어 붙인다."""
    out = []
    for t in tables:
        rows = t["rows"]
        if len(rows) < 3 or any(c["code"] for r in rows for c in r):
            continue
        head = rows[0]
        if len(head) < 2 or not re.match(r"^(구\s*분|종\s*류|명\s*칭)", head[0]["t"]):
            continue
        if any(c["span"] > 1 for c in head[1:]):
            continue
        names = [c["t"] for c in head[1:]]
        data = {n: {} for n in names}
        prev = None
        for r in rows[1:]:
            if len(r) == len(names) + 1:
                prev = r[0]["t"]
                for n, c in zip(names, r[1:]):
                    data[n][prev] = c["t"]
            elif len(r) == len(names) and prev:
                for n, c in zip(names, r):
                    data[n][prev] = (data[n][prev] + " " + c["t"]).strip()
        for n in names:
            out.append((n, data[n], t["pos"]))
    return out


def _nget(d, *keys):
    """주석 표 항목 찾기: 항목명(공백 무시)에 keys 중 하나가 들어 있는 첫 항목 → (항목명, 값)."""
    for k, v in (d or {}).items():
        kk = re.sub(r"\s", "", k)
        if any(x in kk for x in keys):
            return k, v
    return None, None


def _dates(t):
    return [x for x in (kdate(s) for s in re.findall(DATE, t or "")) if x]


def _pct(t):
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", t or "")
    return float(m.group(1)) / 100 if m else None


def _first_num(t):
    m = re.search(r"[\d,]*\d", t or "")
    return num(m.group(0)) if m else None


PREF_RE = r"우선주|종류주식|상환전환|전환상환|상환주식|전환주식|RCPS|CPS"


def _add_years(iso, years, days=0):
    d = dt.date.fromisoformat(add_months(iso, int(round(12 * years))))
    return (d + dt.timedelta(days=days)).isoformat()


def _rel_dates(text, iss, mat=None):
    """기간 문구 → (시작일, 종료일). 날짜가 적혀 있으면 그대로 쓰고, 없으면 발행일 기준 상대 표현을 계산한다.
    '발행일 다음날로부터 3년이 경과한 날부터 존속기간 만료일까지' · '납입기일의 다음날로부터 3년 ~10년 이내' ·
    '발행일(…) 다음날로부터 존속기간인 10년째 되는 날의 전일까지' · '발행일로부터 5년' · '거래완결일의 익일부터 가능'"""
    if not text:
        return None, None
    ds = _dates(text)
    if len(ds) == 1 and iss and ds[0] == iss:
        ds = []
    t = re.sub(DATE, " ", text)
    to_end = bool(re.search(r"만료|만기|존속기간\s*(?:말|종료|만료)", t))
    if len(ds) >= 2:
        return ds[0], ds[1]
    if len(ds) == 1:
        m = re.search(DATE, text)
        if re.match(r"\s*(?:부터|이후|~|-)", text[m.end():m.end() + 6]):
            return ds[0], (mat if to_end or re.search(r"가능|언제든", t) else None)
        return None, ds[0]
    if not iss:
        return None, None
    nxt = bool(re.search(r"다음\s*날|익일", t))
    base = (dt.date.fromisoformat(iss) + dt.timedelta(days=1)).isoformat() if nxt else iss
    before_end = re.search(r"(?:만료|만기)[^0-9]{0,6}(\d{1,3})\s*(개월|년)\s*전", t)
    t2 = t.replace(before_end.group(0), " ") if before_end else t
    yrs = [float(x) / (12 if u == "개월" else 1) for x, u in re.findall(r"(\d{1,3}(?:\.\d+)?)\s*(년|개월)", t2)]

    def end_adj(e):
        if e and before_end and e == mat:
            n = int(before_end.group(1)) * (12 if before_end.group(2) == "년" else 1)
            return add_months(e, -n)
        if e and re.search(r"(?:말일|만료일|만기일)\s*(?:의\s*)?전일|전일\s*까지", t) and e == mat:
            return (dt.date.fromisoformat(e) - dt.timedelta(days=1)).isoformat()
        return e
    if len(yrs) >= 2:
        return _add_years(base, yrs[0]), _add_years(base, yrs[1], -1)
    if len(yrs) == 1:
        y = yrs[0]
        if re.search(r"경과|지난|이후", t):
            return _add_years(base, y), end_adj(mat)
        if re.search(r"째\s*되는\s*날의\s*전일", t):
            return base, _add_years(iss, y, -1)
        if re.search(r"이내|동안|까지", t):
            return base, _add_years(base, y, -1)
        return base, _add_years(iss, y)
    if re.search(r"부터|이후|가능|언제든", t):
        return base, end_adj(mat)
    return None, None


def _rcps_from_text(p):
    """표 없이 본문 문장으로 쓴 종류주식 (일반기업회계기준 감사보고서 등):
    '…800백만원(주식수 32,000주, 1주당 발행가액 25,000원)의 전환상환우선주를 발행하였습니다. … 2025년 12월 27일부터 … 상환을 청구 …'"""
    out = []
    pat = (r"([\d,]+)\s*(백만원|천원|원)\s*\(\s*주식수\s*([\d,]+)\s*주\s*,\s*1\s*주\s*당\s*발행\s*가(?:액|격)\s*([\d,]+)\s*원\s*\)"
           r"\s*의\s*((?:전환상환|상환전환)\s*우선주)")
    for m in re.finditer(pat, p):
        seg = p[m.start():m.end() + 1800]
        pre = p[max(0, m.start() - 160):m.start()]
        N = {"발행주식수": f"{m.group(3)}주", "주당발행가": f"{m.group(4)}원", "총발행금액": m.group(1) + m.group(2)}
        mpar = re.search(r"1\s*주\s*당\s*(?:액면\s*)?금액\s*(?:은\s*)?(?:각각\s*)?([\d,]+)\s*원", p[max(0, m.start() - 1500):m.start()])
        if mpar:
            N["액면금액"] = f"{mpar.group(1)}원"
        md = re.search(r"발행일\s*\(\s*([^)]*?\d{4}[^)]*)\)", seg) or re.search(r"(\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일)", pre)
        if md:
            N["발행일"] = md.group(1).strip()
        ms = re.search(r"존속기간(?:인|은)?\s*\d+\s*년", seg)
        if ms:
            N["존속기간"] = ms.group(0)
        mc = re.search(r"[^.]*보통주(?:식)?\s*으로\s*전환할\s*수\s*있는\s*권리[^.]*", seg)
        if mc:
            N["전환청구기간"] = mc.group(0).strip()
        mp = re.search(r"[^.]*상환\s*을\s*청구할\s*수\s*있는\s*권리[^.]*(?:\.[^.]*(?:복리|단리)[^.]*)?", seg)
        if mp:
            N["상환청구기간"] = mp.group(0).strip()
        mdv = re.search(r"[^.]*(?:누적적|참가적)[^.]*", seg)
        if mdv:
            N["배당"] = mdv.group(0).strip()
        iss = kdate(N.get("발행일"))
        out.append({"name": f"{re.sub(r'\s', '', m.group(5))} ({iss or '발행일 미상'} 발행)", "codes": {}, "labels": {},
                    "note": N, "pos": m.start()})
    return out


def _cur_pref_shares(p):
    """주석의 '우선주의 주식수 변동' 표 → 기말 우선주 수 (종류가 하나일 때만 쓴다)."""
    m = re.search(r"우선주(?:식)?의\s*(?:발행\s*)?주식\s*수\s*(?:의\s*)?변동.{0,400}?기\s*말\s*([\d,]+)", p)
    return num(m.group(1)) if m else None


def rcps_series(rcept_no):
    """종류주식 회차별 원자료: 표준 양식(ACODE) + 주석 조건표(열 = 회차 또는 '구분 | 내용') + 본문 문장."""
    T = xml_tables(rcept_no)
    std = []
    for t in T:
        codes, labels = {}, {}
        for r in t["rows"]:
            plain_cells = [c["t"] for c in r if not c["code"]]
            lab = plain_cells[-1] if plain_cells else ""
            for c in r:
                if c["code"] and c["code"] not in codes:
                    codes[c["code"]], labels[c["code"]] = c["t"], lab
        if "ISS_DATE" in codes:
            std.append({"codes": codes, "labels": labels, "pos": t["pos"]})

    def is_pref(n, d):
        """우선주(종류주식) 조건표인지: 열 이름이나 '종류' 항목에 우선주·종류주식이 있고, 사채·선택권 표가 아니어야 한다."""
        kind_val = _nget(d, "종류", "명칭")[1] or ""
        if re.search(r"사채|신주인수권|선택권", n + " " + kind_val):
            return False
        return bool(re.search(PREF_RE, n + " " + kind_val))

    def series_name(n, d):
        """'구분 | 내용' 표처럼 열 이름이 일반 단어면 '종류' 항목 값을 이름으로 쓴다."""
        if re.fullmatch(r"(내\s*용|금\s*액|조\s*건|세\s*부\s*내\s*용|주요\s*내용)", n.strip()):
            return (_nget(d, "종류", "명칭")[1] or n).strip()
        return n

    notes, seen_n = [], set()
    for n, d, pos in _transposed(T):
        if _nget(d, "상환", "전환")[0] and _nget(d, "발행")[0] and not _nget(d, "행사가", "부여")[0] and is_pref(n, d):
            nm = series_name(n, d)
            if nm not in seen_n:
                seen_n.add(nm)
                notes.append((nm, d, pos))

    def shares_of(d):
        for k, v in d.items():
            if re.search(r"주식수|의결권", k):
                m = re.search(r"([\d,]{2,})\s*주", v)
                if m:
                    return num(m.group(1))
        return None

    out, used = [], set()
    for i, s in enumerate(std):
        sh = num(s["codes"].get("ISS_TOTK"))
        iss = kdate(s["codes"].get("ISS_DATE"))
        match = next((x for x in notes if x[0] not in used and sh and shares_of(x[1]) and abs(shares_of(x[1]) - sh) < 0.5), None)
        if not match:
            match = next((x for x in notes if x[0] not in used and _near(kdate(_nget(x[1], "발행일")[1] or ""), iss)), None)
        if match:
            used.add(match[0])
        out.append({"name": match[0] if match else f"종류주식 {i + 1} ({iss})", "codes": s["codes"], "labels": s["labels"],
                    "note": match[1] if match else {}, "pos": s["pos"]})
    for n, d, pos in notes:
        if n not in used:
            out.append({"name": n, "codes": {}, "labels": {}, "note": d, "pos": pos})
    try:
        p = plain(rcept_no)
    except Exception:
        p = ""
    if not out:
        out = _rcps_from_text(p)
    if len(out) == 1 and not out[0]["codes"]:
        out[0]["cur_shares"] = _cur_pref_shares(p)
    return out


def parse_rcps(s, rc):
    """종류주식 한 회차 → 입력 항목(근거 포함). Temp RCPS 실무 입력례를 따른다:
    만기 = 존속기간 만료일, 만기보장R = 상환 이율(연복리), 표면R 0%, Put = 주주의 상환청구(연 12회),
    IPO 공모가 기준 조정은 '공모가조정'(격자에 리픽싱 미반영)."""
    f = {}

    def put(key, value, evid=None, note=None, conf="high"):
        f[key] = {"value": value, "evidence": [evid] if evid else [], "note": note, "conf": conf}

    C, L, N = s.get("codes") or {}, s.get("labels") or {}, s.get("note") or {}

    def cev(code, text=None):
        return ev(rc, L.get(code) or "", text or C.get(code)) if C.get(code) else None

    def nev(k, v, text=None):
        return ev(rc, k, text or v) if (k and v) else None

    name = s["name"]
    tm = re.search(r"(\d+(?:\s*[-~]\s*\d+)?)\s*(회|종|차)", name)
    bd = (re.sub(r"\s", "", tm.group(1)) + ("" if tm.group(2) == "회" else tm.group(2))) if tm else name
    from_table = bool(N) and not re.search(r"(발행|납입)\)$", name)
    put("회차", bd, ev(rc, "구분", name) if from_table else None, conf="high" if from_table else "mid",
        note=None if tm else "회차 번호가 없어 이름을 그대로 씁니다.")

    k_i, v_i = _nget(N, "발행일", "납입일", "거래완결일")
    iss_n = kdate(v_i) if v_i else None
    iss = kdate(C.get("ISS_DATE")) or iss_n
    if C.get("ISS_DATE"):
        put("발행일", iss, cev("ISS_DATE"),
            note=f"주석 표의 발행일({iss_n})과 다릅니다 — 납입일·발행일 기준을 확인하세요." if iss_n and iss_n != iss else None)
    else:
        put("발행일", iss, nev(k_i, v_i), conf="high" if iss else "low", note=None if iss else "발행일을 찾지 못했습니다.")

    k_sh, v_sh = _nget(N, "발행주식수", "주식수")
    sh_n = _first_num(v_sh)
    shares = num(C.get("ISS_TOTK")) or sh_n
    k_t, v_t = _nget(N, "발행금액", "발행총액")
    price_t = None
    if v_t:
        mm = re.search(r"\(\s*([\d,]+)\s*원\s*\)", v_t)
        price_t = num(mm.group(1)) if mm else None
    k_pr, v_pr = _nget(N, "주당발행가", "1주당발행가", "발행가액", "발행가격")
    price_r = None
    if v_pr and _first_num(v_pr):
        x = _first_num(v_pr)
        per_share = bool(re.search(r"주당|/\s*주|1\s*주", (k_pr or "") + v_pr)) or not (shares and x > 1e7 and x / shares >= 100)
        price_r = x if per_share else x / shares
    k_cp, v_cp = _nget(N, "전환가격", "전환가액")
    conv_x = _first_num(v_cp) if (v_cp and (_first_num(v_cp) or 0) >= 100) else None
    k_c, v_c = _nget(N, "전환에관한", "전환조건", "전환비율")
    adj_k, adj_v = _nget(N, "가액조정", "가격조정", "주요전환")
    conv_txt = " ".join(x for x in (C.get("STK_CHA"), v_c, adj_v) if x)
    ratio = None
    mr = re.search(r"1\s*주\s*당\s*(?:보통주\s*)?(?:식\s*)?([\d.]+)\s*주", conv_txt)
    if mr:
        ratio = float(mr.group(1))
    cht = num(C.get("STK_CHT"))
    if ratio is None and cht and shares:
        ratio = cht / shares
    tot_raw = _first_num(v_t)
    price = num(C.get("ISS_PRC")) or price_t or price_r
    price_how = None
    if price is None and conv_x and ratio:
        price, price_how = conv_x * ratio, f"전환가 {conv_x:,.0f}원 × 전환비율 {ratio:g}"
    if price is None and tot_raw and shares:
        for unit in (1, 1e3, 1e6):
            if 100 <= tot_raw * unit / shares <= 1e8:
                price, price_how = tot_raw * unit / shares, f"총발행금액 ÷ 발행주식수 (금액 단위 {int(unit):,}원)"
                break
    if C.get("ISS_PRC"):
        put("주당발행가", price, cev("ISS_PRC"))
    elif price_t:
        put("주당발행가", price, nev(k_t, v_t, f"{price_t:,.0f}"))
    elif price_r:
        put("주당발행가", price, nev(k_pr, v_pr, re.search(r"[\d,]*\d", v_pr).group(0)))
    else:
        put("주당발행가", price, None, conf="mid" if price else "low", note=price_how or "주당 발행가를 찾지 못했습니다.")
    k_par, v_par = _nget(N, "액면")
    par = num(C.get("ISS_PRCK"))
    if par is None and v_sh:
        mm = re.search(r"\(\s*([\d,]+)\s*원\s*\)", v_sh)
        par = num(mm.group(1)) if mm else None
    if par is None and v_par:
        par = _first_num(v_par)
        put("액면가", par, nev(k_par, v_par, f"{par:,.0f}") if par else None, conf="high" if par else "low")
    else:
        put("액면가", par, cev("ISS_PRCK") or nev(k_sh, v_sh, f"{par:,.0f}" if par else None), conf="high" if par else "low")
    put("발행주식수", shares, cev("ISS_TOTK") or nev(k_sh, v_sh, f"{sh_n:,.0f}" if sh_n else None), conf="high" if shares else "low")
    tot = num(C.get("ISS_TOT"))
    if tot is not None:
        put("발행총액", tot, cev("ISS_TOT"))
    elif shares and price:
        tot = shares * price
        unit_note = next((f"주석 표 금액 {tot_raw:,.0f}은 {u}원 단위" for u, mul in (("천", 1e3), ("백만", 1e6))
                          if tot_raw and abs(tot_raw * mul - tot) / tot < 0.01), None)
        put("발행총액", tot, nev(k_t, v_t, f"{tot_raw:,.0f}") if tot_raw else None, conf="high" if tot_raw else "mid",
            note=f"발행주식수 {shares:,.0f}주 × 주당 발행가 {price:,.0f}원" + (f" ({unit_note})" if unit_note else ""))
    else:
        tot = tot_raw
        put("발행총액", tot, nev(k_t, v_t, f"{tot_raw:,.0f}") if tot_raw else None, conf="mid" if tot else "low",
            note="금액 단위를 확인하세요." if tot else "발행총액을 찾지 못했습니다.")
    pnt = num(C.get("ISS_PNT"))
    cur = num(C.get("ISS_PNTK")) or s.get("cur_shares")
    if pnt is not None:
        put("잔여총액", pnt, cev("ISS_PNT"),
            note="보고서 기준일 현재 잔액 (전환·상환 반영). 평가기준일이 보고서 기준일과 다르면 그 사이 전환·상환을 확인하세요.")
    elif cur and price:
        put("잔여총액", cur * price, ev(rc, "기말", f"{cur:,.0f}"), conf="high",
            note=f"우선주 기말 {cur:,.0f}주 × 주당 발행가 {price:,.0f}원 (주석의 우선주 주식수 변동표)")
    else:
        put("잔여총액", tot, None, conf="mid", note="현재 잔액 표시가 없어 발행총액을 넣었습니다. 전환·상환 여부를 확인하세요.")

    mat = kdate(C.get("TRS_PRD"))
    k_m, v_m = _nget(N, "존속기간", "만기", "유효기간")
    if mat:
        put("만기일", mat, cev("TRS_PRD"), note="종류주식 존속기간(우선주 권리 유효기간) 만료일 — Temp 만기일(AG)")
    elif v_m and [d for d in _dates(v_m) if d != iss]:
        mat = [d for d in _dates(v_m) if d != iss][-1]
        put("만기일", mat, nev(k_m, v_m))
    elif v_m and iss and _rel_dates(v_m, iss)[1]:
        mat = _rel_dates(v_m, iss)[1]
        put("만기일", mat, nev(k_m, v_m), conf="mid", note=f"'{v_m[:40]}' → 발행일 기준으로 계산했습니다.")
    else:
        ends = [e for e in (_rel_dates(_nget(N, "상환기간", "상환청구기간")[1], iss)[1],
                            _rel_dates(_nget(N, "전환기간", "전환청구기간")[1], iss)[1]) if e]
        mat = max(ends) if ends else (add_months(iss, 120) if iss else None)
        put("만기일", mat, None, conf="low",
            note=("존속기간 표시가 없어 상환·전환 기간 끝으로 두었습니다." if ends else
                  "존속기간을 찾지 못해 발행일 + 10년으로 두었습니다 (RCPS 관행).") + " 정관·계약서를 확인하세요.")

    mp = re.search(r"전환\s*가(?:격|액)\s*(?:은|는|:)?\s*(?:1주당\s*)?([\d,]{3,})\s*원", conv_txt)
    if conv_x:
        conv, conv_ev, note_cv, cconf = conv_x, nev(k_cp, v_cp, re.search(r"[\d,]*\d", v_cp).group(0)), None, "high"
    elif mp:
        conv, conv_ev, note_cv, cconf = num(mp.group(1)), nev(k_c, v_c, mp.group(0)) or cev("STK_CHA", mp.group(0)), None, "high"
    else:
        conv = price / ratio if (price and ratio) else price
        if mr and C.get("STK_CHA") and mr.group(0) in C["STK_CHA"]:
            conv_ev = cev("STK_CHA", mr.group(0))
        elif mr:
            conv_ev = nev(k_c, v_c, mr.group(0))
        else:
            conv_ev = None
        note_cv = (f"주당 발행가 {price:,.0f}원 ÷ 전환비율 {ratio:g} (우선주 1주 → 보통주 {ratio:g}주)" if (price and ratio) else
                   "전환가·전환비율 표시가 없어 주당 발행가(1:1)로 두었습니다. 계약서를 확인하세요.")
        cconf = "high" if ratio else "low"
    put("최초전환가격", conv, conv_ev, note=note_cv, conf=cconf)
    put("현조정행사가격", conv, conv_ev, conf="mid",
        note="전환가 조정(희석화·IPO) 사유가 생기지 않았다고 보고 최초 전환가를 씁니다. 조정 내역을 확인하세요.")
    k_d, v_d = _nget(N, "전환기간", "전환청구기간")
    src_d = v_d or C.get("STK_CDA")
    c0, c1 = _rel_dates(src_d, iss, mat)
    explicit = len(_dates(src_d or "")) >= 2
    e = nev(k_d, v_d) if v_d else cev("STK_CDA")
    e0 = cev("_CDA0") if (not v_d and C.get("_CDA0")) else e
    e1 = cev("_CDA1") if (not v_d and C.get("_CDA1")) else e
    put("전환초일", c0 or iss, e0 if c0 else None, conf="high" if explicit else ("mid" if c0 else "low"),
        note=None if explicit else (f"'{(src_d or '')[:50]}' → 발행일 기준으로 계산" if c0 else "전환청구기간이 없어 발행일로 두었습니다."))
    c1 = c1 or ((dt.date.fromisoformat(mat) - dt.timedelta(days=1)).isoformat() if mat else None)
    put("전환말일", c1, e1 if (explicit or c0) else None, conf="high" if explicit else "mid",
        note=None if explicit else "존속기간 말일 전일 또는 문구로 계산 — 확인하세요.")

    pct = None
    if re.search(r"IPO|공모|상장", conv_txt):
        mq = re.search(r"(?:IPO|공모)[^.]{0,40}?(\d{2})\s*%", conv_txt)
        mt = re.search(r"(\d{2})\s*%\s*에\s*해당하는\s*금액으로\s*(?:\S+\s*)?조정", conv_txt)
        pct = int(mt.group(1)) if mt else (int(mq.group(1)) if mq else 70)
        if mt:
            mq = mt
        if mq and C.get("STK_CHA") and mq.group(0) in C["STK_CHA"]:
            e = cev("STK_CHA", mq.group(0))
        else:
            e = nev(k_c, v_c, mq.group(0) if mq else None)
        put("상하향조정", "공모가조정", e, conf="mid",
            note=f"IPO 공모가의 {pct}% 기준 전환가 조정 — Temp는 '공모가조정'·'공모가*{pct}%'로 두고 격자에 리픽싱을 넣지 않습니다.")
        put("Refix최소", None, None, conf="mid", note=f"공모가*{pct}% (Temp AU 문자 입력)")
    elif re.search(r"시가[^.]{0,30}(?:하락|하회)|매\s*\d+\s*개월[^.]{0,40}조정", conv_txt):
        mq = re.search(r"(\d{2})\s*%\s*(?:이상|을\s*하한|를\s*하한)", conv_txt)
        put("상하향조정", "하", nev(k_c, v_c) or cev("STK_CHA"), conf="mid",
            note="시가 하락 시 전환가 조정 조항 — 조정 주기·첫 조정일을 확인하세요.")
        put("Refix최소", round(conv * int(mq.group(1)) / 100, 4) if (mq and conv) else None, None, conf="low",
            note=f"최초 전환가 × {mq.group(1)}%" if mq else "하한을 찾지 못했습니다.")
    else:
        put("상하향조정", "일반", None, conf="mid", note="시가하락·IPO 조정 조항이 보이지 않아 '일반' (희석화 방지 조정만 있으면 Temp '일반')")
        put("Refix최소", None, None, conf="mid")
    f["공모가하한"] = {"value": (pct / 100) if pct else None, "evidence": [], "note": None, "conf": "mid" if pct else "none"}
    put("전환가조정연회수", 12, None, conf="mid", note="Temp 기준 12")
    put("전환가격조정시작일", None, None, conf="none")

    rpay = C.get("STK_RPAY") or _nget(N, "상환권자")[1] or ""
    k_r, v_r = _nget(N, "상환에관한", "상환조건", "상환방법")
    k_p, v_p = _nget(N, "상환기간", "상환청구기간")
    k_rp, v_rp = _nget(N, "상환가액", "상환가격", "상환할증", "상환이율", "상환수익", "보장수익", "상환율", "상환금액")
    rtxt = " ".join(x for x in (v_r, v_rp, C.get("STK_DAT"), C.get("STK_REP"), C.get("STK_PRO"), C.get("STK_PRC"), v_p) if x)
    holder = bool(re.search(r"주주|투자자|인수인", rpay)) or bool(re.search(r"상환\s*을?\s*청구|상환청구", " ".join(
        [v_r or "", v_p or "", C.get("STK_REP") or ""] + list(N.keys()))))
    issuer = bool(re.search(r"회사|발행인|발행회사", rpay)) and not holder
    guessed = False
    if not holder and not issuer and (v_p or v_rp):
        holder, guessed = True, True
    rt = rate_text(rtxt)
    if rt:
        y, comp, _, rnote = parse_rate(rt)
    elif v_rp and _pct(v_rp) is not None:
        y, rt = _pct(v_rp), re.search(r"[\d.]+\s*%", v_rp).group(0)
        comp = "단리" if "단리" in v_rp else "연복리"
        rnote = None if re.search(r"복리|단리", v_rp) else "복리·단리 표시 없음 → 연복리(기본값)"
    else:
        y, comp, rnote = 0.0, "연복리", "상환 이율 표시를 찾지 못해 0%로 두었습니다 — 계약서를 확인하세요."
    rev = None
    if rt:
        for kk, vv in ((k_r, v_r), (k_rp, v_rp), (k_p, v_p)):
            if vv and rt in vv:
                rev = nev(kk, vv, rt)
                break
        rev = rev or ev(rc, "상환", rt)
    src_p = v_p or C.get("STK_DAT")
    p0, p1 = _rel_dates(src_p, iss, mat)
    p_explicit = len(_dates(src_p or "")) >= 2
    pev = nev(k_p, v_p) or cev("STK_DAT")
    bet = C.get("STK_BET") or _nget(N, "배당")[1] or ""
    put("표면이자율", 0.0, None, conf="mid", note=f"우선배당({bet[:40]}) — Temp RCPS 입력례는 표면R 0%")
    put("연간이자지급횟수", 1, None, conf="mid")
    if holder:
        lvl = "high" if rt else "low"
        put("만기이자율", y, rev, note="Temp RCPS 입력례: 만기보장R = 상환 이율", conf=lvl)
        put("상환활증방식", comp, rev, note=rnote, conf=lvl)
        put("Put초일", p0, pev, conf=("high" if p_explicit else "mid") if p0 else "low",
            note="계약상 상환청구 시작일 (Temp AX)" + ("" if p_explicit else f" — '{(src_p or '')[:50]}' → 발행일 기준 계산")
                 + (" · 상환권자 표시가 없어 투자자 상환청구로 봄" if guessed else ""))
        put("Put말일", p1, pev, conf=("high" if p_explicit else "mid") if p1 else "low")
        put("Put연회수", 12, None, conf="mid", note="상환기간 중 언제든지 청구 → Temp 입력례 연 12회")
        put("YTP", y, rev, conf=lvl)
        put("YTP복리", comp, rev, note=rnote, conf=lvl)
    else:
        put("만기이자율", 0.0, None, conf="mid", note="주주의 상환청구권이 없습니다.")
        put("상환활증방식", "연복리", None, conf="mid")
        put("Put초일", None, None, conf="mid", note="주주의 상환청구권 없음")
    put("RedemR", None, None, conf="none")
    put("사모여부", "사모", None, conf="mid", note="종류주식은 특정 투자자 배정 — 사모 곡선이 원칙")
    if issuer:
        put("Call초일", p0, pev, conf="mid", note="회사의 상환권(상환권자: 회사)을 Call로 봅니다.")
        put("Call말일", p1, pev, conf="mid")
        put("Call연회수", 12, None, conf="mid")
        put("YTC", y, rev, conf="mid")
        put("YTC복리", comp, rev, conf="mid")
        put("Call한도", 1.0, None, conf="mid")
        put("Call순위", "선순위", None, conf="mid", note="Temp 기준 선순위 — 상환 통지 후 전환 허용 여부를 확인하세요.")
        put("Call3자배정", "N", None, conf="mid")
    else:
        put("Call초일", None, None, conf="mid", note="회사의 상환권·매도청구권 표시가 없습니다.")
        put("Call한도", 0, None, conf="mid")
    put("배당가능초일", p0 if holder else None, None, conf="mid",
        note="상환은 배당가능이익 한도 안에서만 됩니다(상법 제345조). 이익잉여금이 있다고 보고 계약 상환청구 시작일과 같게 둡니다.")
    f["_raw"] = {"value": {"상환권자": rpay, "배당": bet, "기타": C.get("ISS_ETC"), "전환조건": conv_txt[:300],
                           "콜": any(re.search(r"매도\s*청구|콜\s*옵션|Call", k + " " + v) for k, v in N.items())},
                 "evidence": [], "conf": "none", "note": None}
    return f


def _cha(x):
    """'제 1 차'·'1차'·'주식선택권 1차' → '1' (차수 번호로 표끼리 맞춘다)."""
    m = re.search(r"(\d+)\s*차", x or "")
    return m.group(1) if m else re.sub(r"\s", "", x or "")


def so_grants(rcept_no):
    """주식기준보상 주석: 부여 조건표 + 평가 가정표 + 변동내역(기말 수량)을 차수별로 합친다.
    분기·반기보고서는 연결·별도 주석에 같은 표가 두 번 나오므로 차수 번호로 한 번만 쓴다."""
    T = xml_tables(rcept_no)
    tr = _transposed(T)
    terms = [(n, d) for n, d, _ in tr if _nget(d, "행사가")[0]
             and _nget(d, "부여일", "부여수량", "행사기간", "행사시작", "부여주식")[0]
             and not _nget(d, "공정가치", "변동성")[0]]
    assum = [(n, d) for n, d, _ in tr if _nget(d, "공정가치")[0] and _nget(d, "변동성", "기초자산", "주가")[0]]
    left = {}
    for t in T:
        rows = t["rows"]
        if len(rows) < 3 or any(c["code"] for r in rows for c in r) or not re.match(r"^구\s*분", rows[0][0]["t"]):
            continue
        head = rows[0][1:]
        if not head or not all(c["span"] == 2 for c in head) or not any(("선택권" in c["t"]) or ("차" in c["t"]) for c in head):
            continue
        for r in rows[1:]:
            if re.sub(r"\s", "", r[0]["t"]) in ("기말", "당기말", "분기말", "반기말", "기말잔액") and len(r) == 1 + 2 * len(head):
                for i, c in enumerate(head):
                    left.setdefault(_cha(c["t"]), num(r[1 + 2 * i]["t"]))
                break
    by_date = _so_left_by_date(T)
    try:
        p = plain(rcept_no)
        mm = re.search(r"(이항\s*(?:옵션)?\s*(?:가격결정)?\s*모(?:형|델)|블랙\s*[-·]?\s*숄즈|Black\s*[-–]?\s*Scholes|몬테\s*카를로)", p)
        model = mm.group(1) if mm else None
    except Exception:
        model = None
    out, seen = [], set()
    for i, (n, d) in enumerate(terms):
        if _cha(n) in seen:
            continue
        seen.add(_cha(n))
        a = next((d2 for n2, d2 in assum if _cha(n2) == _cha(n)), None)
        if a is None and len(assum) == len(terms):
            a = assum[i][1]
        lf, lsrc = left.get(_cha(n)), "변동내역 표의 기말 수량"
        if lf is None:
            gd = kdate(_nget(d, "부여일")[1] or "")
            hit = gd if gd in by_date else next((x for x in by_date if gd and abs((dt.date.fromisoformat(x) - dt.date.fromisoformat(gd)).days) <= 3), None)
            if hit:
                lf, lsrc = by_date[hit], f"'주식매수선택권 부여·행사현황' 표의 기말 미행사수량 합 (부여일 {hit})"
        out.append({"name": n, "terms": d, "assum": a or {}, "left": lf, "left_src": lsrc, "model": model})
    return out


def _so_left_by_date(T):
    """사업·분기보고서 본문 '주식매수선택권의 부여 및 행사현황' 표(부여받은 자별 행) → 부여일별 기말 미행사수량 합.
    머리 행의 '당기변동수량(행사·취소)'처럼 두 칸짜리 머리가 있어 칸 위치는 span을 더해 맞춘다."""
    by_date, seen = {}, set()
    for t in T:
        rows = t["rows"]
        if len(rows) < 3:
            continue
        head = rows[0]
        names = [re.sub(r"\s", "", c["t"]) for c in head]
        if not (any("부여일" in x for x in names) and any(x.startswith("기말") for x in names) and any("행사가" in x for x in names)):
            continue
        pos, i = {}, 0
        for c, x in zip(head, names):
            pos.setdefault(x, i)
            i += c["span"] or 1
        width = i
        pd_ = next(v for x, v in pos.items() if "부여일" in x)
        pl = next(v for x, v in pos.items() if x.startswith("기말"))
        sig = tuple(c["t"] for r in rows[1:4] for c in r)
        if sig in seen:
            continue
        seen.add(sig)
        for r in rows[1:]:
            if len(r) != width:
                continue
            gd = kdate(r[pd_]["t"])
            if gd:
                by_date[gd] = by_date.get(gd, 0) + (num(r[pl]["t"]) or 0)
    return by_date


def parse_so(g, rc):
    """주식선택권 한 차수 → 입력 항목(근거 포함). 회사 평가 가정(주석)을 함께 담아 재계산에 쓴다."""
    f = {}

    def put(key, value, evid=None, note=None, conf="high"):
        f[key] = {"value": value, "evidence": [evid] if evid else [], "note": note, "conf": conf}

    D, A = g["terms"], g["assum"]

    def nev(k, v, text=None):
        return ev(rc, k, text or v) if (k and v) else None

    n = g["name"]
    tm = re.search(r"(\d+)\s*차", n)
    put("회차", f"{tm.group(1)}차" if tm else n, ev(rc, "구분", n))
    k, v = _nget(D, "부여일")
    put("부여일", kdate(v) if v else None, nev(k, v), conf="high" if v else "low")
    k, v = _nget(D, "행사가")
    put("행사가격", num(v) if v else None, nev(k, v), conf="high" if v else "low")
    k1, v1 = _nget(D, "행사시작", "가득일")
    k2, v2 = _nget(D, "행사종료", "만료일", "행사만료")
    if not v1 or not v2:
        k3, v3 = _nget(D, "행사기간", "행사가능기간")
        ds = _dates(v3)
        if len(ds) >= 2:
            put("행사시작일", ds[0], nev(k3, v3))
            put("행사종료일", ds[1], nev(k3, v3))
    if "행사시작일" not in f:
        put("행사시작일", kdate(v1) if v1 else None, nev(k1, v1), conf="high" if v1 else "low")
        put("행사종료일", kdate(v2) if v2 else None, nev(k2, v2), conf="high" if v2 else "low")
    k, v = _nget(D, "부여수량", "부여주식", "부여수")
    put("부여수량", num(v) if v else None, nev(k, v), conf="high" if v else "low")
    left = g.get("left")
    put("잔여수량", left if left is not None else (num(v) if v else None),
        ev(rc, "기말", f"{left:,.0f}") if left is not None else None,
        conf="high" if left is not None else "low",
        note=(g.get("left_src") or "변동내역 표의 기말 수량") if left is not None else "기말 수량을 찾지 못해 부여수량을 넣었습니다.")
    k, v = _nget(D, "가득조건", "가득기간")
    put("가득조건", v, nev(k, v), conf="high" if v else "none")
    k, v = _nget(D, "결제방식", "결제")
    put("결제방식", "현금결제형" if (v and "현금" in v) else "주식결제형", nev(k, v), conf="high" if v else "mid",
        note=None if v else "결제방식 표시가 없어 주식결제형으로 봅니다.")
    k, v = _nget(A, "기초자산", "주가")
    put("회사S", num(v) if v else None, nev(k, v), conf="high" if v else "none")
    k, v = _nget(A, "변동성")
    put("회사변동성", _pct(v), nev(k, v), conf="high" if v else "none")
    k, v = _nget(A, "무위험")
    put("회사Rf", _pct(v), nev(k, v), conf="high" if v else "none")
    k, v = _nget(A, "옵션만기", "만기", "존속기간")
    put("회사만기", kdate(v) if (v and kdate(v)) else None, nev(k, v) if (v and kdate(v)) else None,
        conf="high" if (v and kdate(v)) else "none", note=(f"주석 표기: {v}" if v and not kdate(v) else None))
    k, v = _nget(A, "배당")
    if not v or re.fullmatch(r"[-–\s0.%원]*", v):
        dv, dnote = 0.0, None
    elif "%" in v:
        dv, dnote = _pct(v), None
    else:
        dv, dnote = None, f"기대배당 '{v}' — 금액이면 수익률(배당 ÷ 주가)로 바꿔 넣으세요."
    put("기대배당", dv, nev(k, v) if (v and dv) else None, conf="high" if v else "mid", note=dnote)
    k, v = _nget(A, "공정가치")
    put("회사FV", num(v) if v else None, nev(k, v), conf="high" if v else "none",
        note="회사가 주석에 쓴 1개당 공정가치 — 재계산 결과와 대조")
    f["_raw"] = {"value": {"모형": g.get("model")}, "evidence": [], "conf": "none", "note": None}
    return f


OPT_KW = r"풋\s*옵션|콜\s*옵션|Put\s*Option|Call\s*Option|매수\s*청구권|매도\s*청구권"


def option_agreements(rc):
    """주석의 풋·콜옵션 약정 문장. 사채·종류주식 조건, 목차 제목('주주간 약정 및 재무약정 사항 등')은 뺀다.
    매수청구권(상대에게 사 달라고 할 권리) = 풋, 매도청구권(팔라고 할 권리) = 콜."""
    try:
        p = plain(rc)
    except Exception:
        return []
    out = []
    for s in re.split(r"(?<=다\.)\s+", p):
        if not re.search(OPT_KW, s, re.I) or re.search(r"사채|신주인수권|상환전환|전환상환|종류주식|우선주|투자\s*판단에\s*참고|"
                                                        r"주식\s*소유\s*현황|발행주식\s*총수|지정\s*매수자", s):
            continue
        if len(s) > 900:
            continue
        if not re.search(r"행사\s*가|가격|\d\s*원|%|연복리|\d\s*주|지분", s):
            continue
        s = s.strip()[:700]
        put = bool(re.search(r"풋|매수\s*청구|Put", s, re.I))
        call = bool(re.search(r"콜|매도\s*청구|Call", s, re.I))
        out.append({"name": s[:36], "type": "Call" if (call and not put) else "Put", "text": s})
    return out[:6]


def parse_opt(g, rc):
    """풋·콜옵션 한 건 → 입력 항목. 약정 문장에서 날짜·이율·가격·주식수를 뽑고, 없으면 비워 두어 계약서로 채우게 한다."""
    f = {}

    def put(key, value, evid=None, note=None, conf="high"):
        f[key] = {"value": value, "evidence": [evid] if evid else [], "note": note, "conf": conf}

    t = (g or {}).get("text") or ""
    kw = re.search(OPT_KW, t, re.I)

    def tev(x):
        return ev(rc, kw.group(0) if kw else "", x) if (t and x) else None

    put("회차", (g or {}).get("name") or "직접 입력", tev(t[:40]) if t else None, conf="mid" if t else "none")
    put("옵션종류", (g or {}).get("type") or "Put", tev(kw.group(0)) if kw else None, conf="mid" if t else "low",
        note="매수청구권(사 달라고 할 권리)=Put, 매도청구권(팔라고 할 권리)=Call — 권리자 입장에서 고르세요.")
    ds = _dates(t)
    put("행사시작일", ds[0] if ds else None, tev(re.search(DATE, t).group(0)) if ds else None, conf="mid" if ds else "low")
    put("행사종료일", ds[1] if len(ds) > 1 else None, None, conf="mid" if len(ds) > 1 else "low")
    rt = rate_text(t)
    if rt:
        y, comp, _, rnote = parse_rate(rt)
        put("행사가증가율", y, tev(rt), note=rnote, conf="mid")
        put("행사가복리", comp, tev(rt), conf="mid")
    else:
        put("행사가증가율", 0.0, None, conf="low", note="행사가가 '투자원금 × (1 + 연 x%)'처럼 붙으면 이율을 넣으세요.")
        put("행사가복리", "연복리", None, conf="low")
    mp = re.search(r"주당\s*([\d,]{3,})\s*원", t)
    put("행사가격", num(mp.group(1)) if mp else None, tev(mp.group(0)) if mp else None, conf="mid" if mp else "low",
        note=None if mp else "주당 행사가격(또는 투자단가)을 넣으세요.")
    ms = re.search(r"([\d,]{2,})\s*주(?!당|식|주)", t)
    put("수량", num(ms.group(1)) if ms else None, tev(ms.group(0)) if ms else None, conf="mid" if ms else "low")
    put("행사가기산일", None, None, conf="low", note="행사가에 이자가 붙는 기산일(투자일·계약일). 비우면 평가기준일.")
    typ = (g or {}).get("type") or "Put"
    put("할인", "Rf" if typ == "Call" else "Rd", None, conf="low",
        note="Temp SO 시트는 Put이면 Rd(SO후속=Y), Call이면 Rf로 할인합니다. 옵션 종류를 바꾸면 계산 때 따라 바뀌고, 직접 고르면 그 값을 씁니다.")
    put("권리자", None, None, conf="none")
    put("의무자", None, None, conf="none")
    f["_raw"] = {"value": {"문장": t}, "evidence": [], "conf": "none", "note": None}
    return f


RIGHTS_MAP = {"ISS_DATE": "PYM_DT", "ISS_PRC": "PST_ISS_VAL", "ISS_PRCK": "FVAL", "ISS_TOTK": "PST_CNT", "TRS_PRD": "EXT_DT",
              "STK_DAT": "RDT_PRD", "STK_PRC": "RDT_VAL", "STK_CHT": "CVT_CNT", "STK_BET": "DVD_INFO"}


def _codes(rc):
    codes, labels = {}, {}
    for t in xml_tables(rc):
        for r in t["rows"]:
            plain_cells = [c["t"] for c in r if not c["code"]]
            lab = plain_cells[-1] if plain_cells else ""
            for c in r:
                if c["code"] and c["code"] not in codes:
                    codes[c["code"]], labels[c["code"]] = c["t"], lab
    return codes, labels


def rights_series(rc):
    """유상증자결정 한 건 → 종류주식 원자료 (기타주식이 없거나 종류주식이 아니면 None)."""
    K, LB = _codes(rc)
    cnt = num(K.get("PST_CNT"))
    if not cnt or not re.search(PREF_RE, (K.get("STO_CNT") or "") + " " + (K.get("RDT_CDT") or "") + (K.get("EXE_FUNC") or "")):
        return None
    C, L = {}, {}
    for std, src in RIGHTS_MAP.items():
        if K.get(src) not in (None, "", "-"):
            C[std], L[std] = K[src], LB.get(src, "")
    if num(K.get("PST_ISS_VAL")):
        C["ISS_TOT"], L["ISS_TOT"] = f"{cnt * num(K['PST_ISS_VAL']):,.0f}", "기타주식 × 발행가"
    holder = re.search(r"(?:대상자|주주|투자자|인수인)[^.]{0,160}상환\s*을?\s*청구", K.get("RDT_CDT") or "")
    issuer = re.search(r"(?:발행인|회사)[^.]{0,30}(?:은|는)[^.]{0,40}상환할\s*수", K.get("RDT_CDT") or "")
    C["STK_RPAY"] = "주주" if holder else ("회사" if issuer else "")
    C["STK_REP"] = " ".join(x for x in (K.get("RDT_CDT"), K.get("RDT_MTD")) if x and x != "-")
    L["STK_REP"] = LB.get("RDT_CDT", "상환조건")
    C["STK_CHA"] = " ".join(x for x in (K.get("EXE_FUNC"), K.get("EXE_REG")) if x and x != "-")
    L["STK_CHA"] = LB.get("EXE_REG", "전환가액 조정에 관한 사항")
    if K.get("CVT_BGN_DT") and K.get("CVT_END_DT"):
        C["STK_CDA"], L["STK_CDA"] = f"{K['CVT_BGN_DT']} ~ {K['CVT_END_DT']}", LB.get("CVT_BGN_DT", "전환청구기간")
        C["_CDA0"], L["_CDA0"] = K["CVT_BGN_DT"], LB.get("CVT_BGN_DT", "시작일")
        C["_CDA1"], L["_CDA1"] = K["CVT_END_DT"], LB.get("CVT_END_DT", "종료일")
    N = {}
    if K.get("EXE_PRC") not in (None, "", "-"):
        N["전환가액"] = K["EXE_PRC"]
    ext = " / ".join(f"{lab}: {K[c]}" for c, lab in (("OPT_FCT", "옵션"), ("ETC_CTR_INFO", "기타 약정"))
                     if K.get(c) not in (None, "", "-", "해당없음"))
    C["ISS_ETC"] = ext
    iss = kdate(K.get("PYM_DT"))
    return {"name": f"{K.get('STO_CNT') or '종류주식'} ({iss} 납입)", "codes": C, "labels": L, "note": N, "pos": 0,
            "rights": {"board": kdate(K.get("DRC_DT")), "pay": iss, "part": K.get("PART"), "cnt": cnt,
                       "opt": K.get("OPT_FCT"), "etc": K.get("ETC_CTR_INFO"), "method": K.get("CI_MTH")}}


def rights_filings(items):
    """유상증자결정 공시 (같은 이사회결의는 가장 최근 정정본만)."""
    out, seen = [], set()
    for d in items:
        nm = d["report_nm"]
        if not re.search(r"유상\s*증자\s*결정", nm) or re.search(r"종속회사|자회사", nm):
            continue
        try:
            s = rights_series(d["rcept_no"])
        except Exception:
            continue
        if not s:
            continue
        key = (s["rights"]["board"], s["rights"]["pay"])
        if key in seen:
            continue
        seen.add(key)
        s["rcept"] = {"rcept_no": d["rcept_no"], "date": _iso(d["rcept_dt"]), "report_nm": nm.strip()}
        out.append(s)
    return out


def _near(a, b, days=5):
    try:
        return bool(a and b and abs((dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days) <= days)
    except ValueError:
        return False


def call_terms(text, rc):
    """'발행회사의 매도청구권(Call option)에 관한 사항' 문단 → Call 한도·기간·주기·이율·일정표 (근거 포함)."""
    from dart import exercise_table
    out = {}
    if not text:
        return out
    m = re.search(r"(?:발행가액|전자등록|권면)\s*총액[^%]{0,40}?의\s*(\d+(?:\.\d+)?)\s*%", text) \
        or re.search(r"(?:인수\s*금액|발행\s*금액|투자\s*금액|보유\s*주식(?:\s*수)?)[^%.]{0,20}?의\s*(\d+(?:\.\d+)?)\s*%", text) \
        or re.search(r"행사\s*한도[^%.]{0,40}?(\d+(?:\.\d+)?)\s*%", text)
    if m:
        out["Call한도"] = (float(m.group(1)) / 100, ev(rc, "매도청구권", m.group(0)))
    ds = [kdate(x) for x in re.findall(r"\(\s*(" + DATE[1:-1] + r")\s*\)", text)]
    ds = [d for d in ds if d]
    if len(ds) >= 2:
        out["Call초일"] = (ds[0], ev(rc, "매도청구권", re.search(r"\(\s*" + DATE[1:-1], text).group(0)[1:].strip()))
        out["Call말일"] = (ds[1], None)
    else:
        mr = re.search(DATE + r"\s*(?:부터|~|－|-)\s*" + DATE + r"\s*(?:까지)?", text)
        if mr and kdate(mr.group(1)) and kdate(mr.group(2)):
            out["Call초일"] = (kdate(mr.group(1)), ev(rc, "매도청구권", mr.group(1)))
            out["Call말일"] = (kdate(mr.group(2)), None)
    mf = re.search(r"매\s*(\d+)\s*개월", text)
    if mf:
        out["Call연회수"] = (round(12 / int(mf.group(1)), 4), ev(rc, "매도청구권", mf.group(0)))
    mp = re.search(r"(?:매매대금|행사금액|매도청구\s*수익률)[^.]{0,200}?((?:분기\s*단위|\d+\s*개월\s*단위)?\s*연\s*(?:복리|단리)\s*[\d.]+\s*%|[\d.]+\s*%\s*\(\s*연\s*(?:복리|단리)\s*\))", text)
    if mp:
        t = mp.group(1).replace("분기단위", "3개월 단위").replace("분기 단위", "3개월 단위")
        y, comp, _, note = parse_rate(t)
        out["YTC"] = (y, ev(rc, "매매대금", mp.group(1)))
        out["YTC복리"] = (comp, ev(rc, "매매대금", mp.group(1)))
    else:
        mq = re.search(r"(?:매매\s*(?:대금|가액)|행사\s*금액)[^.]{0,120}?연\s*([\d.]+)\s*%[^.]{0,40}?"
                       r"((?:분기|반기|\d+\s*개월)\s*단위)?\s*(?:연\s*)?(복리|단리)", text)
        if mq:
            unit = mq.group(2) or ""
            comp = "단리" if mq.group(3) == "단리" else ("분복리" if re.search(r"분기|3\s*개월", unit) else
                                                         "반복리" if re.search(r"반기|6\s*개월", unit) else "연복리")
            out["YTC"] = (float(mq.group(1)) / 100, ev(rc, "매도청구권", mq.group(0)[:40]))
            out["YTC복리"] = (comp, None)
    tbl = exercise_table(text)
    if len(tbl) >= 2:
        out["Call일정"] = ([[d, r] for d, r, _ in tbl], ev(rc, "매도청구권 행사금액", tbl[0][2]))
    if re.search(r"발행회사가\s*지정하는\s*자|제3자", text):
        out["Call3자배정"] = ("Y", ev(rc, "매도청구권", re.search(r"발행회사가\s*지정하는\s*자|제3자", text).group(0)))
    return out


PREF_TERMS = r"전환에\s*관한|전환\s*기간|전환\s*가액|전환\s*비율|본건\s*(?:주식|우선주)|인수인|인수\s*금액|발행가액\s*총액|전자등록\s*총액"


def pref_option(g, raws):
    """주석의 옵션 문장이 종류주식 조건의 일부인가 (그러면 별도 풋·콜 카드가 아니라 그 종류주식의 Call·Put이다)."""
    if not raws:
        return False
    t = g.get("text") or ""
    if re.search(PREF_TERMS, t):
        return True
    flat = re.sub(r"\s", "", t)
    for raw in raws:
        body = re.sub(r"\s", "", " ".join(str(v) for v in (raw or {}).values() if v))
        if any(flat[i:i + 25] in body for i in range(0, max(1, len(flat) - 25), 10)):
            return True
        if g.get("type") == "Call" and re.search(r"매도\s*청구|Call|콜\s*옵션", body, re.I):
            return True
    return False


def pref_call_text(rc, raw):
    """종류주식의 Call 조항 원문: 조건표 '기타'·'콜' + 같은 보고서의 콜 약정 문장(이율·매매가액)."""
    parts = [str((raw or {}).get(k) or "") for k in ("기타", "콜")]
    body = " ".join(p for p in parts if p)
    if not re.search(r"매도\s*청구|Call|콜\s*옵션", body, re.I):
        return ""
    for g in option_agreements(rc):
        if g["type"] == "Call" and pref_option(g, [raw]) and g["text"] not in body:
            body += " " + g["text"]
    return body


def apply_pref_call(f, text, rc):
    """종류주식 조건의 매도청구권 → Call 입력 항목 (Call 행사자 지정 공시가 없어 Call초일이 비어 있을 때)."""
    if (f.get("Call초일") or {}).get("value") or not text:
        return False
    ct = call_terms(text, rc)
    if not ct.get("Call초일"):
        return False
    for k, (v, e) in ct.items():
        f[k] = {"value": v, "evidence": [e] if e else [], "conf": "high" if e else "mid",
                "note": "종류주식 조건(주석)의 매도청구권 조항 — 행사자·한도는 계약서로 확인하세요."}
    if "YTC" not in ct:
        f["YTC"] = {"value": 0.0, "evidence": [], "conf": "low", "note": "매도청구권 이율을 찾지 못했습니다. 계약서의 Call 행사가(이율)를 넣으세요."}
    f["Call순위"] = {"value": "선순위", "evidence": [], "conf": "mid",
                   "note": "Temp 기준 선순위 — Call 통지 후 전환 허용 여부를 계약서로 확인하세요."}
    return True


def rcps_events(items, iss, val_date=None):
    """같은 발행일(±5일) 종류주식에 대한 거래소·주요사항 공시 (최신순):
    전환주식의 전환가액 조정·결정 / 전환주식의 전환청구권 행사·종류주식의 보통주 전환 / 전환주식 매수선택권 행사자 지정·제3자 행사."""
    vd = val_date or dt.date.today().isoformat()
    out = []
    for d in items:
        nm = d["report_nm"]
        if not re.search(r"전환주식의\s*전환가액\s*(?:조정|결정)|전환주식의\s*전환청구권\s*행사|종류주식의\s*보통주식\s*으로\s*전환|"
                         r"전환주식\s*매수선택권", nm):
            continue
        rc, date = d["rcept_no"], _iso(d["rcept_dt"])
        try:
            p = plain(rc)
        except Exception:
            continue
        base = {"rcept_no": rc, "report_nm": nm.strip(), "filed": date}
        if re.search(r"전환가액\s*(?:조정|결정)", nm):
            mi = re.search(r"발행일\s*" + DATE, p)
            if not _near(kdate(mi.group(1)) if mi else None, iss):
                continue
            m = re.search(r"조정\s*전\s*전환가액\s*\(원\)\s*조정\s*후\s*전환가액\s*\(원\)\s*(?:비?상장\s*)?([\d,]+)\s+([\d,]+)", p)
            if not m:
                continue
            ap = re.search(r"조정가액\s*적용일\s*" + DATE, p)
            basis = re.search(r"(?:\[조정근거\]|가\.\s*조정\s*근거)(.*?)(?:\[조정방법\]|나\.\s*조정\s*방법|6\.\s*조정가액)", p, re.S)
            flo = re.search(r"(?:최저\s*조정\s*한도|전환가액\s*최저\s*한도|최저\s*한도)[^:：]{0,40}[:：]\s*([\d,]+)\s*원", p)
            out.append({**base, "type": "refix", "date": kdate(ap.group(1)) if ap else date, "before": num(m.group(1)),
                        "after": num(m.group(2)), "basis": (basis.group(1).strip() if basis else "")[:900],
                        "floor": num(flo.group(1)) if flo else None, "ev_floor": ev(rc, "최저", flo.group(1)) if flo else None,
                        "ev": ev(rc, "조정후 전환가액", m.group(2))})
        elif re.search(r"전환청구권\s*행사|보통주식\s*으로\s*전환", nm):
            mi = re.search(DATE + r"\s*(?:에\s*)?발행한", p) or re.search(r"청구일자.{0,80}?" + DATE + r"\s+" + DATE, p)
            iss_x = kdate(mi.group(mi.lastindex)) if mi else None
            if not _near(iss_x, iss):
                continue
            m = re.search(r"우선주\s*가\.\s*전환청구\s*전\s*:\s*([\d,]+)\s*주.{0,60}?감소\s*주식\s*수\s*:\s*([\d,]+)\s*주.{0,40}?"
                          r"전환청구\s*후\s*:\s*([\d,]+)\s*주", p, re.S)
            when = re.search(r"전환청구일\s*:\s*" + DATE, p)
            if not m:
                continue
            out.append({**base, "type": "conversion", "date": kdate(when.group(1)) if when else date,
                        "before_sh": num(m.group(1)), "converted": num(m.group(2)), "after_sh": num(m.group(3)),
                        "ev": ev(rc, "전환청구 후", f"{m.group(3)}주")})
        else:
            K, L = _codes(rc)
            pay = kdate(K.get("CVTK_PAY_DT") or K.get("CVST_DT"))
            if not _near(pay, iss, 40):
                continue
            k = p.find("매도청구권(Call")
            terms = call_terms(p[k:k + 4000] if k >= 0 else "", rc)
            exercise = "제3자" in nm
            out.append({**base, "type": "call_exercise" if exercise else "call_designate",
                        "date": kdate(K.get("EXC_DT") or K.get("EXE_DSG_DT")) or date,
                        "qty": num(K.get("CV_EXC_AMT") or K.get("CB_EXE_AMT")), "total": num(K.get("CV_TOT_AMT") or K.get("CB_TOT_AMT")),
                        "ratio": K.get("EXC_RT") or K.get("CB_EXE_RT"), "who": K.get("CVST_NM") or K.get("ISSU_NM"),
                        "rel": K.get("RLT"), "price": num(K.get("EXC_PRC")), "conv_price": num(K.get("EXE_PRC") or K.get("CV_PRC")),
                        "terms": terms})
    out = [e for e in out if (e.get("date") or "") <= vd] + [dict(e, after_vd=True) for e in out if (e.get("date") or "") > vd]
    out.sort(key=lambda e: (e.get("date") or "", e["rcept_no"]), reverse=True)
    return out


def apply_rcps_events(f, evs, val_date=None):
    """평가기준일까지의 이벤트를 입력 항목에 반영한다 (전환가·리픽싱 조건·잔여 주식·Call 조건)."""
    vd = val_date or dt.date.today().isoformat()
    past = [e for e in evs if not e.get("after_vd")]
    price = (f.get("주당발행가") or {}).get("value")
    rf = next((e for e in past if e["type"] == "refix"), None)
    if rf:
        f["현조정행사가격"] = {"value": rf["after"], "evidence": [rf["ev"]], "conf": "high",
                         "note": f"{rf['date']} 적용 '{rf['report_nm']}' 조정 후 가액 (조정 전 {rf['before']:,.0f})"}
        b = rf["basis"]
        mm = re.search(r"매\s*(\d+)\s*개월", b)
        mp = re.search(r"(\d{2})\s*%\s*이상", b)
        first = (f.get("최초전환가격") or {}).get("value")
        typ = "상하" if re.search(r"상향\s*조정", b) else "하"
        if mm or mp:
            f["상하향조정"] = {"value": typ, "evidence": [ev(rf["rcept_no"], "조정근거", mm.group(0) if mm else mp.group(0))],
                          "conf": "high", "note": "전환주식 전환가액 조정 공시의 조정근거 — " + ("하향 후 상향(최초 전환가까지)" if typ == "상하" else "하향만")}
        if rf.get("floor"):
            f["Refix최소"] = {"value": rf["floor"], "evidence": [rf["ev_floor"]], "conf": "high",
                            "note": "조정 공시의 최저 조정한도 금액 (무상증자 등으로 조정된 발행 당시 전환가 기준)"}
        elif mp and first:
            f["Refix최소"] = {"value": round(first * int(mp.group(1)) / 100, 4), "evidence": [ev(rf["rcept_no"], "조정근거", mp.group(0))],
                            "conf": "high", "note": f"최초 전환가 {first:,.0f} × {mp.group(1)}%"}
        if mm:
            n = int(mm.group(1))
            f["전환가조정연회수"] = {"value": round(12 / n, 4), "evidence": [ev(rf["rcept_no"], "조정근거", mm.group(0))], "conf": "high",
                              "note": f"매 {n}개월 조정"}
            iss = (f.get("발행일") or {}).get("value")
            if iss:
                f["전환가격조정시작일"] = {"value": add_months(iss, n), "evidence": [], "conf": "mid",
                                      "note": f"발행일 + {n}개월 (첫 조정일, 계산)"}
    cv = next((e for e in past if e["type"] == "conversion"), None)
    if cv and price:
        f["잔여총액"] = {"value": cv["after_sh"] * price, "evidence": [cv["ev"]], "conf": "high",
                     "note": f"{cv['date']} 전환청구 후 우선주 {cv['after_sh']:,.0f}주 × 주당 발행가 {price:,.0f}원"}
    cd = next((e for e in evs if e["type"] in ("call_designate", "call_exercise") and e.get("terms")), None)
    if cd and not (f.get("Call초일") or {}).get("value"):
        for k, (v, e) in cd["terms"].items():
            f[k] = {"value": v, "evidence": [e] if e else [], "conf": "high" if e else "mid",
                    "note": f"'{cd['report_nm']}'의 매도청구권 조항"}
        f.setdefault("Call순위", {"value": "선순위", "evidence": [], "conf": "mid",
                                 "note": "Temp 기준 선순위 — Call 통지 후 전환 허용 여부를 계약서로 확인하세요."})
        f["Call순위"] = f["Call순위"] if f["Call순위"].get("value") else {"value": "선순위", "evidence": [], "conf": "mid", "note": None}
    return f


def so_exercise_blocks(items, val_date=None):
    """주식매수선택권 행사 공시의 차수별 부여·행사 현황 (가장 최근 공시 기준): 부여일·총부여·잔여·행사가·행사기간."""
    vd = val_date or dt.date.today().isoformat()
    for d in items:
        if not re.search(r"주식매수선택권\s*행사", d["report_nm"]) or _iso(d["rcept_dt"]) > vd:
            continue
        out = _so_blocks(d["rcept_no"], d["report_nm"].strip(), _iso(d["rcept_dt"]))
        if out:
            return out
    return []


def _so_blocks(rc, report_nm="", date=None):
    """행사 공시 한 건 → 차수별 [(부여일·총부여·잔여·행사가·행사기간)]."""
    try:
        p = plain(rc)
    except Exception:
        return []
    out = []
    if True:
        for m in re.finditer(r"\((\d+)\s*차\)(.{0,700}?)(?=\(\d+\s*차\)|4\.\s|\Z)", p, re.S):
            b = m.group(2)
            g = lambda pat: (re.search(pat, b) or [None, None])[1]
            iss = kdate(g(r"부여일\s*:\s*(" + DATE[1:-1] + ")"))
            if not iss:
                continue
            per = re.search(r"행사기간\s*:\s*(" + DATE[1:-1] + r")\s*~\s*(" + DATE[1:-1] + ")", b)
            out.append({"name": f"{m.group(1)}차", "부여일": iss, "총부여": num(g(r"총\s*부여\s*주식수\s*:\s*([\d,]+)")),
                        "잔여": num(g(r"잔여\s*주식수\s*:\s*([\d,]+)")), "행사가": num(g(r"행사가액\s*:\s*1주당\s*([\d,]+)")),
                        "기간": (kdate(per.group(1)), kdate(per.group(2))) if per else (None, None),
                        "rcept_no": rc, "report_nm": report_nm, "date": date})
        return out


def _rate_pct(x):
    """'4.8238%' · '연 7.48%' · '0.0275'(범위 표의 소수) → 연 %. 'CD+1.5' 같은 변동금리는 None."""
    t = (x or "").replace(",", "").strip()
    m = re.fullmatch(r"(?:연\s*)?(\d{1,2}(?:\.\d+)?)\s*%?", t)
    if not m:
        return None
    v = float(m.group(1))
    if "%" not in t and v < 1:
        v *= 100
    return round(v, 4) if 0.3 <= v <= 30 else None


def borrow_rates(rc):
    """정기보고서 주석의 차입금 이자율 — 차입처별 행('4.82%')과 '이자율 범위' 표(0.0275~0.0748).
    사채 표(CB·BW 표면이율)는 차입원가가 아니므로 뺀다. 연결·별도 주석 중복은 한 번만."""
    out, seen = [], set()
    for t in xml_tables(rc):
        rows = t["rows"]
        body = " ".join(c["t"] for r in rows for c in r)
        if "이자율" not in body or not re.search(r"차입|금융기관|은행", body):
            continue
        if re.search(r"발행회사|증권종류|표시\s*이자율|전환사채|신주인수권|교환사채|회사채", body):
            continue
        head = " ".join(c["t"] for c in rows[0])
        kind_all = "장기" if "장기" in head else ("단기" if "단기" in head else "")
        for r in rows:
            cells = [c["t"].strip() for c in r]
            lab = cells[0] if cells else ""
            if re.search(r"차입금.*이자율", lab) and len(cells) >= 3:
                lo, hi = _rate_pct(cells[1]), _rate_pct(cells[2])
                for v in (lo, hi):
                    if v is not None:
                        key = ("범위", lab, v)
                        if key not in seen:
                            seen.add(key)
                            out.append({"kind": "장기" if "장기" in lab else "단기", "lender": "이자율 범위", "rate": v, "amount": None})
                continue
            rates = [_rate_pct(x) for x in cells if re.search(r"%", x)]
            rates = [v for v in rates if v is not None]
            if not rates:
                continue
            lender = next((x for x in cells if re.search(r"은행|금융|캐피탈|증권|보험|공사|공단|진흥|기금|저축|\(주\)|㈜|농협|수협", x)), "")
            amts = [num(x) for x in cells if re.fullmatch(r"\(?[\d,]{4,}\)?", x)]
            kind = "장기" if any("장기" in x for x in cells) else ("단기" if any("단기" in x for x in cells) else kind_all)
            key = (lender, rates[0], amts[0] if amts else None)
            if key in seen:
                continue
            seen.add(key)
            out.append({"kind": kind, "lender": lender or (cells[1] if len(cells) > 1 else ""), "rate": rates[0], "amount": amts[0] if amts else None})
    vals = sorted(x["rate"] for x in out)
    return {"items": out[:40], "n": len(vals), "min": vals[0] if vals else None, "max": vals[-1] if vals else None,
            "median": vals[len(vals) // 2] if vals else None}


DERIV_RE = r"파생\s*(?:상품|금융)?\s*(?:금융)?\s*부채|파생상품부채|파생금융부채|내재파생"
_AMT = re.compile(r"^\(?-?[\d,]{3,}\)?$")


def _amt(x):
    t = (x or "").strip()
    if not _AMT.match(t):
        return None
    v = num(t)
    return -abs(v) if t.startswith("(") else v


def _series_re(bd_tm):
    n = re.sub(r"\D", "", str(bd_tm or ""))
    return re.compile(rf"(?<!\d)0*{n}\s*(?:회|차)(?!\d)") if n else None


def _unit(vals, face):
    """표 금액 단위 맞추기: 표의 발행가액(일부 상환·전환되면 잔액)이 발행총액의 2~102%가 되는 배수 (원·천원·백만원)."""
    for v in vals:
        if v and face:
            for k in (1, 1000, 1e6):
                if face * 0.02 <= v * k <= face * 1.02:
                    return k
    return None


def book_values(rc, kind, bd_tm, face):
    """정기보고서 주석에서 회차의 장부 값: 파생상품부채(공정가치), 사채 장부금액(상각후원가), 평가 가정(주가·변동성·할인율).
    회차별 열 표 → 회차 행 → (회차 구분이 없으면) 공정가치 서열체계의 파생상품부채 합계 순으로 찾는다."""
    sre = _series_re(bd_tm)
    out = {"derivative": None, "bond_book": None, "unit": None, "src": None, "scope": None, "assume": {}, "model": None,
           "total_derivative": None, "series_seen": set()}
    T = xml_tables(rc)
    pref = kind in ("RCPS", "CPS")
    for t in T:
        rows = t["rows"]
        if not rows:
            continue
        head = [c["t"] for c in rows[0]]
        for r in rows:
            m = re.search(r"제\s*(\d+)\s*회", r[0]["t"]) if r else None
            if m and re.search(r"사채|전환|신주인수권|교환", r[0]["t"]):
                out["series_seen"].add(m.group(1))
        for c in head:
            m = re.fullmatch(r"\s*제?\s*(\d+)\s*회(?:차)?\s*", c)
            if m:
                out["series_seen"].add(m.group(1))
        if not sre or pref:
            continue
        col = next((i for i, c in enumerate(head) if sre.search(c) and len(c) < 30), None)
        if col is not None and out["derivative"] is None:
            lab = {re.sub(r"\s", "", r[0]["t"]): r for r in rows[1:] if r}
            issue = next((r for k, r in lab.items() if re.match(r"(발행가액|액면금액|권면)", k)), None)
            u = _unit([_amt(issue[col]["t"])] if issue and len(issue) > col else [], face)
            drow = next((r for k, r in lab.items() if re.search(DERIV_RE, k) and len(r) > col), None)
            brow = next((r for k, r in lab.items() if re.match(r"장부금액", k) and len(r) > col), None)
            if drow is not None and (u or face is None):
                u = u or 1
                out.update({"derivative": (_amt(drow[col]["t"]) or 0) * u, "unit": u, "src": "회차별 열 표", "scope": "회차"})
                if brow is not None:
                    out["bond_book"] = (_amt(brow[col]["t"]) or 0) * u
                continue
        for r in rows[1:]:
            if not r or not sre.search(r[0]["t"]):
                continue
            cells = [c["t"] for c in r]
            if out["derivative"] is None and any(re.search(DERIV_RE, x) for x in cells[1:3]):
                v = next((_amt(x) for x in cells[1:] if _amt(x) is not None), None)
                if v is not None:
                    out.update({"derivative": v, "src": "회차 행 (단위 확인)", "scope": "회차"})
        if out["bond_book"] is None:
            blk = None
            for r in rows[1:]:
                if not r:
                    continue
                lab0 = re.sub(r"\s", "", r[0]["t"])
                if re.search(r"사채", r[0]["t"]) and re.search(r"제\s*\d+\s*회", r[0]["t"]):
                    blk = bool(sre.search(r[0]["t"]))
                    if blk:
                        fv = next((_amt(c["t"]) for c in r[1:] if _amt(c["t"]) is not None), None)
                        out["_face_cell"] = fv
                    continue
                if blk and re.match(r"(합계|장부금액|소계)", lab0):
                    v = next((_amt(c["t"]) for c in r[1:] if _amt(c["t"]) is not None), None)
                    u = _unit([out.get("_face_cell")], face) or 1
                    if v is not None:
                        out["bond_book"] = v * u
                    blk = None
        if col is not None:
            for r in rows[1:]:
                if not r or len(r) <= col:
                    continue
                k = re.sub(r"\s", "", r[0]["t"])
                for key, pat in (("주가", r"기초자산가격|주가"), ("전환가", r"전환가격|행사가격"), ("변동성", r"변동성"),
                                 ("무위험", r"무위험"), ("할인율", r"위험이자율|할인율|신용위험")):
                    if re.search(pat, k) and key not in out["assume"] and not k.startswith("금리") \
                            and not (key == "할인율" and "무위험" in k):
                        out["assume"][key] = r[col]["t"].strip()
    for t in T:
        rows = t["rows"]
        head = " ".join(c["t"] for c in rows[0]) if rows else ""
        if re.search(r"수준\s*3|Level\s*3", head, re.I):
            for r in rows[1:]:
                lab0 = r[0]["t"] if r else ""
                want = r"(?:상환)?전환우선주\s*부채|우선주\s*부채|종류주식\s*부채" if pref else DERIV_RE
                if re.search(want, lab0):
                    vals = [_amt(c["t"]) for c in r[1:] if _amt(c["t"]) is not None]
                    if vals and out["total_derivative"] is None:
                        out["total_derivative"] = vals[-1]
            if out["total_derivative"] is not None:
                break
    for t in T:
        rows = t["rows"]
        if rows and any("가치평가방법" in c["t"] or "평가기법" in c["t"] for c in rows[0]):
            for r in rows[1:]:
                if r and re.search(DERIV_RE if not pref else r"우선주", r[0]["t"]):
                    mc = next((c["t"] for c in r[1:] if re.search(r"모형|모델|이항|Tsiveriotis|Black|몬테|격자|할인법|Lattice", c["t"], re.I)), None)
                    if mc:
                        out["model"] = mc.strip()[:40]
                        break
    n_series = len(out["series_seen"])
    if out["derivative"] is None and out["total_derivative"] is not None:
        if n_series <= 1 or pref:
            out.update({"derivative": out["total_derivative"], "src": "공정가치 서열체계 합계", "scope": "회차(단일)" if n_series <= 1 else "합계"})
        else:
            out.update({"src": "공정가치 서열체계 합계", "scope": "합계"})
    out["series_seen"] = sorted(out["series_seen"], key=lambda x: int(x))
    out.pop("_face_cell", None)
    return out


ALL = ("CB", "BW", "EB", "RCPS", "CPS", "SO", "PUT", "CALL")
SOURCES = [
    (r"전환주식\s*매수선택권|제3자의\s*전환주식\s*매수선택권", ("RCPS", "CPS", "CALL"), "Call",
     "종류주식 Call(매수선택권) 행사자 지정·행사 — Call 한도·행사자·잔여 주식"),
    (r"전환사채\s*매수선택권|제3자의\s*전환사채\s*매수선택권", ("CB", "CALL"), "Call", "CB Call 행사자 지정·행사 — Call 한도·잔액"),
    (r"전환주식의\s*전환가액\s*결정", ("RCPS", "CPS"), "가액조정", "종류주식 전환가 결정"),
    (r"전환주식의\s*전환가액\s*조정", ("RCPS", "CPS"), "가액조정", "종류주식 전환가 조정 (IPO·희석화·리픽싱)"),
    (r"전환주식의\s*전환청구권\s*행사|종류주식의\s*보통주식\s*으로\s*전환|복수의결권주식의\s*보통주식\s*전환",
     ("RCPS", "CPS"), "잔액변동", "종류주식 → 보통주 전환 (잔여 주식 감소)"),
    (r"자기전환주식\s*(?:취득|매도)", ("RCPS", "CPS"), "잔액변동", "종류주식 자기주식 취득·매도 (잔여 주식 변동)"),
    (r"특수관계인에\s*대한\s*전환사채\s*발행", ("CB",), "발행조건", "특수관계인 대상 CB 발행 — Call·특수관계자 거래 검토"),
    (r"전환형\s*조건부\s*자본증권", ("CB",), "참고", "조건부자본증권(코코본드) — 상각·전환 사유가 달라 대시보드 평가 대상 아님"),
    (r"전환사채권?\s*발행\s*결정|전환사채\s*발행\s*결[의정]", ("CB",), "발행조건", "CB 발행결정 — 전환가·Put·Call·리픽싱 조건"),
    (r"신주인수권부사채권?\s*발행\s*결정", ("BW",), "발행조건", "BW 발행결정 — 행사가·Put·Call 조건"),
    (r"교환사채권?\s*발행\s*결정", ("EB",), "발행조건", "EB 발행결정 — 교환대상·교환가"),
    (r"전환사채\s*전환가액\s*결정", ("CB",), "가액조정", "CB 전환가 결정"),
    (r"(?:전환|교환|행사)가액(?:의)?\s*조정|전환가액ㆍ신주인수권행사가액ㆍ교환가액의\s*조정", ("CB", "BW", "EB"), "가액조정",
     "전환·행사·교환가 조정 (리픽싱·희석화)"),
    (r"전환청구권\s*행사|전환청구권ㆍ신주인수권ㆍ교환청구권\s*행사|신주인수권\s*행사|교환청구권\s*행사", ("CB", "BW", "EB"), "잔액변동",
     "전환·행사·교환 청구 (잔액 감소)"),
    (r"만기\s*전\s*사채\s*취득|자기전환사채\s*만기\s*전\s*취득|사채\s*취득", ("CB", "BW", "EB"), "잔액변동",
     "만기 전 취득 — Put·Call 행사, 소각·재매각 여부"),
    (r"자기전환사채\s*매도", ("CB",), "잔액변동", "취득한 CB 재매각 (잔액 증가)"),
    (r"유상\s*증자\s*결정(?!.*(?:종속회사|자회사))", ("RCPS", "CPS"), "발행조건", "유상증자결정 — 종류주식 발행이면 '기타주식에 관한 사항'에 상환·전환 조건"),
    (r"주식매수선택권\s*(?:부여|부여에\s*관한)", ("SO",), "발행조건", "주식선택권 부여 — 부여일·행사가·수량·행사기간"),
    (r"주식매수선택권\s*(?:행사|취소)", ("SO",), "잔액변동", "주식선택권 행사·취소 (잔여 수량 감소)"),
    (r"타법인\s*주식\s*및\s*출자증권\s*(?:양수|양도|취득|처분)|주식\s*양수도|영업\s*양수", ("PUT", "CALL"), "약정",
     "지분 거래 — 풋·콜옵션·우선매수권 약정이 붙는 경우가 많음"),
    (r"주주\s*간\s*계약|풋\s*옵션|콜\s*옵션|매수\s*청구권\s*약정|매도\s*청구권", ("PUT", "CALL"), "약정",
     "옵션 약정 (자율공시·기타경영사항)"),
    (r"주식\s*등의\s*대량\s*보유\s*상황\s*보고서", ("CB", "BW", "EB", "RCPS", "CPS", "PUT", "CALL"), "지분",
     "5% 보고 — CB·BW 보유자, 보유주식 관련 계약(콜·풋·담보) 기재"),
    (r"임원\s*ㆍ?\s*주요\s*주주\s*특정\s*증권\s*등\s*소유\s*상황|최대\s*주주\s*등\s*소유\s*주식\s*변동", ALL, "지분",
     "임원·주요주주 보유 변동 — Call 행사로 취득한 CB, SO 행사·전환 확인"),
    (r"증권\s*신고서\s*\(\s*채무\s*증권|투자\s*설명서.*채무|일괄\s*신고\s*추가\s*서류", ("CB", "BW", "EB"), "발행조건",
     "증권신고서(채무증권) — 공모 CB·BW 조건·신용등급·투자위험"),
    (r"증권\s*신고서\s*\(\s*지분\s*증권|투자\s*설명서", ("RCPS", "CPS", "SO"), "발행조건",
     "증권신고서(지분증권)·투자설명서 — IPO 공모가 → RCPS 공모가 리픽싱, 상장 전 SO·종류주식 현황"),
    (r"증권\s*발행\s*실적\s*보고서", ("CB", "BW", "EB", "RCPS", "CPS"), "발행조건", "증권발행실적 — 실제 발행금액·배정 결과"),
    (r"^(?!.*(?:종속회사|자회사)).*?(?:부도\s*발생|회생\s*절차|파산|기한\s*의?\s*이익\s*상실|원리금\s*(?:의\s*)?미지급|채권\s*은행\s*등의\s*관리|영업\s*정지|"
     r"해산\s*사유|자본\s*잠식|자기\s*자본\s*50)", ALL, "신용",
     "부도·회생·기한이익상실·자본잠식 → 신용등급·Rd, 조기상환(Put) 사유"),
    (r"매출액\s*또는\s*손익\s*구조|손익\s*구조\s*\d+%", ("CB", "BW", "EB", "RCPS", "CPS"), "신용",
     "손익구조 변동 — 결손이면 RCPS 상환 불가(상법 345조), 신용 판단"),
    (r"매매\s*거래\s*정지|거래\s*정지|관리\s*종목|상장\s*적격성|상장\s*폐지|투자\s*주의\s*환기|정리\s*매매", ALL, "시장",
     "거래정지·관리종목·상장폐지 사유 → 주가·변동성 신뢰성, Put 행사 사유"),
    (r"주식\s*병합|주식\s*분할|무상\s*증자|감자\s*결정|무액면|액면주식", ALL, "조정사유",
     "병합·분할·무상증자·감자·액면 변경 → 전환가·주식수 조정"),
    (r"회사\s*(?:분할\s*)?합병\s*결정|회사\s*분할\s*결정|주식\s*(?:의\s*)?포괄적\s*교환|주식\s*교환\s*ㆍ?\s*이전|주식\s*매수\s*청구권",
     ALL, "조정사유", "합병·분할·주식교환 → 전환가·대상주식 조정, 반대주주 주식매수청구권"),
    (r"최대주주\s*변경", ("CB", "RCPS", "CPS", "CALL"), "참고", "최대주주 변경 — Call 행사자·지배구조"),
    (r"자기\s*주식\s*처분\s*결정", ("SO", "EB"), "참고", "자기주식 처분 — SO 행사 교부, EB 교환대상"),
    (r"(?:정기|임시)?\s*주주\s*총회\s*(?:소집\s*결의|결과)", ("SO", "RCPS", "CPS"), "참고",
     "주주총회 — SO 부여 승인, 정관(종류주식·SO 한도) 변경"),
    (r"사업보고서|반기보고서|분기보고서", ALL, "주석", "정기보고서 — 종류주식 발행현황·주석(조건·잔액·평가 가정)"),
    (r"감사보고서", ALL, "주석", "감사보고서 주석 (비상장 외감법인)"),
]
ROLE_ORDER = ["발행조건", "가액조정", "잔액변동", "Call", "약정", "지분", "조정사유", "신용", "시장", "주석", "참고"]


def classify(report_nm):
    """보고서명 → (상품들, 역할, 설명). 해당 없으면 None."""
    nm = re.sub(r"^\[[^\]]*\]\s*", "", report_nm.strip())
    for pat, kinds, role, desc in SOURCES:
        if re.search(pat, nm):
            return kinds, role, desc
    return None


def related(items, kind, limit=80):
    """평가 대상 상품과 관련된 공시만 골라 역할을 붙인다 (최신순)."""
    out = []
    for d in items:
        c = classify(d["report_nm"])
        if c and kind in c[0]:
            out.append({"rcept_no": d["rcept_no"], "date": _iso(d["rcept_dt"]), "report_nm": d["report_nm"].strip(),
                        "role": c[1], "desc": c[2]})
    return out[:limit]


def note_instruments(corp_code, val_date=None, items=None):
    """정기보고서(평가기준일에 맞는 것)에서 RCPS·CPS·SO 목록 → 화면의 회차 카드."""
    items = items if items is not None else disclosures(corp_code)
    rep = pick_report(items, val_date)
    out = []
    if not rep:
        return {"report": None, "items": _rights_items(items, val_date, [])}
    rc = rep["rcept_no"]
    src = f"{rep['report_nm']} 주석"
    vd = val_date or dt.date.today().isoformat()
    raws = []
    for s in rcps_series(rc):
        f = parse_rcps(s, rc)
        if not any((f.get(k) or {}).get("value") for k in ("발행일", "잔여총액", "주당발행가", "발행총액")):
            continue
        raws.append((f.get("_raw") or {}).get("value") or {})
        redeem = bool(f.get("Put초일", {}).get("value") or f.get("Call초일", {}).get("value"))
        out.append({"kind": "RCPS" if redeem else "CPS", "rcept_no": rc, "key": s["name"], "bd_tm": f["회차"]["value"],
                    "bd_knd": s["name"], "bd_fta": f"{(f['잔여총액']['value'] or 0):,.0f}", "bddd": f["발행일"]["value"],
                    "price": f"{(f['현조정행사가격']['value'] or 0):,.0f}", "mtd": f["만기일"]["value"], "src": src, "note": True})
    for g in so_grants(rc):
        f = parse_so(g, rc)
        q = f["잔여수량"]["value"]
        end = f["행사종료일"]["value"]
        out.append({"kind": "SO", "rcept_no": rc, "key": g["name"], "bd_tm": f["회차"]["value"], "bd_knd": g["name"],
                    "bd_fta": f"{q:,.0f}주" if q is not None else "", "bddd": f["부여일"]["value"],
                    "price": f"{(f['행사가격']['value'] or 0):,.0f}", "mtd": end,
                    "src": src + (" · 행사기간 종료" if end and end < vd else ""), "note": True})
    if not any(x["kind"] == "SO" for x in out):
        for b in so_exercise_blocks(items, val_date):
            out.append({"kind": "SO", "rcept_no": b["rcept_no"], "key": f"EXR:{b['name']}", "bd_tm": b["name"], "bd_knd": b["name"],
                        "bd_fta": f"{b['잔여']:,.0f}주" if b["잔여"] is not None else "", "bddd": b["부여일"],
                        "price": f"{(b['행사가'] or 0):,.0f}", "mtd": b["기간"][1], "src": f"{b['report_nm']} ({b['date']})",
                        "note": True})
    for g in option_agreements(rc):
        if pref_option(g, raws):
            continue
        out.append({"kind": "OPT", "rcept_no": rc, "key": g["name"], "bd_tm": f"{g['type']} 약정", "bd_knd": g["name"],
                    "bd_fta": "", "bddd": "", "price": "", "mtd": "", "src": src, "note": True})
    out += _rights_items(items, val_date, out, rep["period"])
    out.append({"kind": "OPT", "rcept_no": rc, "key": "MANUAL", "bd_tm": "직접 입력", "bd_knd": "풋·콜옵션 (계약서 조건 입력)",
                "bd_fta": "", "bddd": "", "price": "", "mtd": "", "src": "계약서 조건으로 Put·Call 평가 (Temp SO 시트 방식)",
                "note": True})
    return {"report": rep, "items": out}


def _rights_items(items, val_date, have, after=None):
    """보고서에 아직 없는 종류주식(보고서 기준일 뒤 발행)을 유상증자결정 공시로 보탠다.
    보고서 기준일 전에 납입됐는데 보고서에 없으면 이미 전환·상환된 회차로 보고 넣지 않는다."""
    vd = val_date or dt.date.today().isoformat()
    known = [(x["bddd"], num(x["bd_fta"])) for x in have if x["kind"] in ("RCPS", "CPS")]
    out = []
    for s in rights_filings(items):
        pay = s["rights"]["pay"]
        if not pay or pay > vd or (after and pay <= after):
            continue
        if any(b and abs((dt.date.fromisoformat(b) - dt.date.fromisoformat(pay)).days) <= 5 for b, _ in known):
            continue
        rc = s["rcept"]["rcept_no"]
        f = parse_rcps(s, rc)
        redeem = bool(f.get("Put초일", {}).get("value") or f.get("Call초일", {}).get("value"))
        out.append({"kind": "RCPS" if redeem else "CPS", "rcept_no": rc, "key": "RIGHTS", "bd_tm": f"{pay} 납입",
                    "bd_knd": s["name"], "bd_fta": f"{(f['잔여총액']['value'] or 0):,.0f}", "bddd": pay,
                    "price": f"{(f['현조정행사가격']['value'] or 0):,.0f}", "mtd": f["만기일"]["value"],
                    "src": f"{s['rcept']['report_nm']} ({s['rcept']['date']})", "note": True})
    return out


def find_instrument(rc, kind, key):
    """보고서 rc에서 kind·key(열 이름)로 원자료 한 건과 입력 항목을 찾는다. key 'RIGHTS'는 유상증자결정 공시."""
    if key == "RIGHTS":
        s = rights_series(rc)
        return (s, parse_rcps(s, rc)) if s else (None, None)
    if kind == "SO" and key.startswith("EXR:"):
        b = next((x for x in _so_blocks(rc) if x["name"] == key[4:]), None)
        if not b:
            return None, None
        g = {"name": f"{b['name']} 주식선택권", "terms": {"부여일": b["부여일"], "행사가격": f"{b['행사가'] or ''}",
                                                    "행사기간": f"{b['기간'][0]} ~ {b['기간'][1]}", "부여수량": f"{b['총부여'] or ''}"},
             "assum": {}, "left": b["잔여"], "model": None}
        return g, parse_so(g, rc)
    if kind == "OPT":
        if key == "MANUAL":
            return None, parse_opt(None, rc)
        g = next((x for x in option_agreements(rc) if x["name"] == key), None)
        return (g, parse_opt(g, rc)) if g else (None, None)
    if kind == "SO":
        g = next((x for x in so_grants(rc) if x["name"] == key), None)
        return (g, parse_so(g, rc)) if g else (None, None)
    s = next((x for x in rcps_series(rc) if x["name"] == key), None)
    return (s, parse_rcps(s, rc)) if s else (None, None)
