"""엑셀 내보내기.
1) 리뷰 양식  : ○○_FV_review_RAS_YYMMDD.xlsx  (재계산 시트 + 근거 시트)
2) Temp DW 행 : Temp_v530 DW 시트와 같은 열(A~FZ). 입력 열만 채우고 Temp가 계산하는 열은 비운다.
"""
import io
import datetime as dt

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import column_index_from_string, get_column_letter

NAVY = "15233B"
ACC = "C85A1E"
THIN = Side(style="thin", color="D3CEC4")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD = PatternFill("solid", fgColor="EDE9E1")
MARK = PatternFill("solid", fgColor="FFF2A8")
MIL = '#,##0,,;[Red]-#,##0,,'

CLASS = {
    "전체가치": ("현금유입", "자산"), "일반사채": ("상각부채", ""), "파생상품 합계": ("", ""),
    "Call": ("별도FV자산", ""), "Put": ("FV부채", ""), "전환권": ("자본가능", "별도부채"),
}


def _date(s):
    if isinstance(s, dt.date):
        return s
    return dt.date.fromisoformat(s) if s else None


def _v(fields, k):
    return (fields.get(k) or {}).get("value")


def _cellable(v):
    """엑셀 칸에 넣을 수 있는 값으로 (일정표 리스트 → '2028-07-10 101.0043% / …')."""
    if isinstance(v, (list, tuple)):
        return " / ".join(f"{d} {r * 100:.4f}%" if isinstance(r, (int, float)) else f"{d} {r}" for d, r in v) or None
    if isinstance(v, dict):
        return str(v)
    return v


def issue_basis_report(rep, face):
    """발행일 기준(리뷰 양식 1.2): 평가사 값도 전체 = 발행금액, 전환권 = 발행금액 − 일반사채 − Put − Call 로 맞춘다
    (리뷰 실무 예: 회사 전환권 = 발행금액 − 일반사채 − Put − Call, Call은 음수라 더해진다)."""
    rep = dict(rep or {})
    if face and all(rep.get(k) is not None for k in ("일반사채", "Put")):
        rep["전체가치"] = face
        rep["전환권"] = face - rep["일반사채"] - rep["Put"] - (rep.get("Call") or 0)
    return rep


def review_xlsx(st):
    m, f, res, rep = st["meta"], st["fields"], st["result"], st.get("report") or {}
    if m.get("issue_basis"):
        rep = issue_basis_report(rep, float(_v(f, "잔여총액") or 0))
    wb = Workbook()
    ws = wb.active
    ws.title = "재계산"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 2
    for col, w in zip("BCDEFGHI", (14, 14, 14, 14, 14, 12, 12, 12)):
        ws.column_dimensions[col].width = w
    ws["B1"] = f"[{m['company']}] 발행 복합금융상품 평가 Review"
    ws["B1"].font = Font(bold=True, size=14, color=NAVY)
    ws["B2"] = f"-평가사 : {m.get('appraiser') or ''}"
    ws["B4"] = "* 하기 재계산 금액을 회사에 회람하는 것은 독립성 훼손위협이 되니 유의하시기 바랍니다. "
    ws["B4"].font = Font(color="B83A3A")
    kind = m.get("kind") or "CB"
    if kind == "OPT":
        ws["B6"] = f"1. {_v(f, '옵션종류') or 'Put'} 옵션 — {m.get('bd_tm') or ''}"
    else:
        ws["B6"] = f"1. {kind} {m.get('bd_tm') or ''}" if kind == "SO" else (
            f"1. {kind} 제{m.get('bd_tm') or ''}회" if kind in ("RCPS", "CPS") else f"1. {m.get('bd_tm') or ''}{kind}")
    ws["B6"].font = Font(bold=True)
    if kind in ("SO", "OPT"):
        _so_block(ws, st, opt=(kind == "OPT"))
    else:
        _cb_block(ws, st, rep)
    _tail(ws, st)
    _evidence_sheets(wb, st)
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _so_block(ws, st, opt=False):
    """주식선택권: 1개당 공정가치(이항 · Temp SO 시트)와 회사 주석 공정가치 대조.
    opt=True: 풋·콜옵션 — 주당 공정가치와 평가사 값 대조 (같은 SO 시트 격자, 할인 Rf/Rd, 행사가 이율)."""
    m, f, res, rep = st["meta"], st["fields"], st["result"] or {}, st.get("report") or {}
    typ = _v(f, "옵션종류") or "Put"
    disc = m.get("discount") or ("Rf" if typ == "Call" else "Rd")
    if opt:
        ws["B7"] = f"1.2. 평가기준일 {m.get('val_date') or ''} ({typ}, 할인 {disc})"
    else:
        ws["B7"] = f"1.2. 측정일 {m.get('measure_date') or ''} ({_v(f, '결제방식') or '주식결제형'})"
    ws["B8"] = "* 가정사항"
    curve = st["curves"].get("rd" if (opt and disc == "Rd") else "rf") or []
    rf = [x for x in curve if x not in (None, "")]
    if opt:
        heads = ["평가기준일", "행사가격", "주가", "변동성(연)", f"할인율({disc})", "행사시작일", "행사종료일", "행사가 이율"]
        vals = [_date(m.get("val_date")), _v(f, "행사가격"), st["market"].get("S"), st["market"].get("sigma"),
                (rf[0] / 100 if len(rf) == 1 else "곡선 입력"), _date(_v(f, "행사시작일")), _date(_v(f, "행사종료일")),
                _v(f, "행사가증가율") or 0]
    else:
        heads = ["측정일", "행사가격", "주가", "변동성(연)", "무위험이자율", "행사시작일", "행사종료일", "기대배당"]
        vals = [_date(m.get("measure_date")), _v(f, "행사가격"), st["market"].get("S"), st["market"].get("sigma"),
                (rf[0] / 100 if len(rf) == 1 else "곡선 입력"), _date(_v(f, "행사시작일")), _date(_v(f, "행사종료일")), _v(f, "기대배당") or 0]
    fmts = ["yyyy-mm-dd", "#,##0", "#,##0", "0.00%", "0.00%", "yyyy-mm-dd", "yyyy-mm-dd", "0.00%"]
    for i, (h, v, fm) in enumerate(zip(heads, vals, fmts)):
        c = ws.cell(9, 2 + i, h); c.fill = HEAD; c.border = BOX; c.font = Font(bold=True)
        c = ws.cell(10, 2 + i, v); c.number_format = fm; c.border = BOX
    for i, h in enumerate(["구분", "대시보드", "회사·평가사", "차이", "차이율"]):
        c = ws.cell(12, 2 + i, h); c.fill = HEAD; c.border = BOX; c.font = Font(bold=True)
    per_co = rep.get("옵션1개당") if rep.get("옵션1개당") is not None else _v(f, "회사FV")
    qL, qG = res.get("잔여수량"), res.get("부여수량")
    if opt:
        rows = [(f"{typ} 주당 (원, 이항·Temp SO 시트)", res.get("옵션1개당"), per_co, "#,##0.0"),
                (f"블랙숄즈 유럽형 {typ} (참고, 원)", res.get("BS유럽형"), None, "#,##0.0")]
    else:
        rows = [("옵션 1개당 (원, 이항·Temp SO)", res.get("옵션1개당"), per_co, "#,##0.0"),
                ("블랙숄즈 유럽형 (참고, 원)", res.get("BS유럽형"), None, "#,##0.0")]
    if qL is not None:
        rows.append((f"{qL:,.0f}주 총액" if opt else f"잔여 {qL:,.0f}개 총액", res.get("전체가치"),
                     per_co * qL if per_co is not None else None, MIL))
    if qG is not None and qG != qL:
        rows.append((f"부여 {qG:,.0f}개 총액", res.get("부여분 총액"), per_co * qG if per_co is not None else None, MIL))
    for r, (k, a, b, fm) in enumerate(rows, start=13):
        d = (a - b) if (a is not None and b is not None) else None
        row = [k, a, b, d, (d / b) if (d is not None and b) else None]
        for i, v in enumerate(row):
            c = ws.cell(r, 2 + i, v); c.border = BOX
            c.number_format = ["@", fm, fm, fm, "0.00%"][i]
    ws.column_dimensions["B"].width = 30


def _cb_block(ws, st, rep):
    m, f, res = st["meta"], st["fields"], st["result"]
    basis = "발행일(전체FV 발행금액 기준)" if m.get("issue_basis") else "평가기준일"
    ws["B7"] = f"1.2. {basis}"
    ws["B8"] = "* 가정사항"
    heads = ["평가일", "잔여발행총액", "신용등급", "연Step", "Refix효과", "주가", "변동성(연)", "현조정행사가격"]
    vals = [_date(m["val_date"]), _v(f, "잔여총액"), m.get("credit_grade"), m.get("steps_per_year"),
            m.get("refix_effect") or "N", st["market"].get("S"), st["market"].get("sigma"), _v(f, "현조정행사가격")]
    fmts = ["yyyy-mm-dd", "#,##0", "@", "0", "@", "#,##0", "0.00%", "#,##0"]
    for i, (h, v, fm) in enumerate(zip(heads, vals, fmts)):
        c = ws.cell(9, 2 + i, h); c.fill = HEAD; c.border = BOX; c.font = Font(bold=True)
        c = ws.cell(10, 2 + i, v); c.number_format = fm; c.border = BOX
    for i, h in enumerate(["구분", "FV(주당)", "FV(백만)", "회사(백만)", "차이(백만)", "발행가대비", "발행회사", "투자자"]):
        c = ws.cell(12, 2 + i, h); c.fill = HEAD; c.border = BOX; c.font = Font(bold=True)
    face = float(_v(f, "잔여총액") or 0)
    unit = 10000.0
    for r, k in enumerate(["전체가치", "일반사채", "파생상품 합계", "Call", "Put", "전환권"], start=13):
        fv = res.get(k)
        co = rep.get(k)
        if k == "파생상품 합계" and co is None and all(rep.get(x) is not None for x in ("Call", "Put", "전환권")):
            co = rep["Call"] + rep["Put"] + rep["전환권"]
        row = [k, fv / face * unit if fv is not None and face else None, fv, co,
               (fv - co) if (fv is not None and co is not None) else None,
               ((fv - co) / face) if (fv is not None and co is not None and face) else None, *CLASS[k]]
        for i, v in enumerate(row):
            c = ws.cell(r, 2 + i, v); c.border = BOX
            c.number_format = ["@", "#,##0", MIL, MIL, MIL, "0.00%", "@", "@"][i]


def _tail(ws, st):
    ws["B20"] = "Con)"
    ws["B20"].font = Font(bold=True)
    ws["B21"] = st.get("conclusion") or "특이사항 없음."
    ws["B23"] = "[질의 및 추가확인 사항]"
    ws["B23"].font = Font(bold=True)
    r = 24
    for a in st.get("alerts") or []:
        if a.get("checked") is False:
            continue
        ws.cell(r, 2, f"- {a['title']}: {a['why']}")
        r += 1
    ws.cell(r + 1, 2, "※ FV는 대시보드 참고 추정치입니다. 확정 값은 Temp_v530 계산 결과로 바꿔 적으세요.").font = Font(italic=True, color="737B8A")


def _evidence_sheets(wb, st):
    f = st["fields"]
    ev = wb.create_sheet("입력근거")
    ev.sheet_view.showGridLines = False
    for i, (h, w) in enumerate([("항목", 16), ("값", 16), ("출처", 10), ("공시 접수번호", 16), ("원문 항목", 18), ("근거 문구(원문 형광펜 부분)", 70), ("메모", 40), ("DART 링크", 50)]):
        c = ev.cell(1, 1 + i, h); c.fill = HEAD; c.font = Font(bold=True); c.border = BOX
        ev.column_dimensions[get_column_letter(1 + i)].width = w
    r = 2
    for k, fv in f.items():
        if k.startswith("_"):
            continue
        evs = fv.get("evidence") or [None]
        for e in evs:
            src = (e or {}).get("src") or ("수동" if fv.get("manual") else ("계산·기본값" if not e else "공시"))
            row = [k, _cellable(fv.get("value")), "수동수정" if fv.get("manual") else src, (e or {}).get("rcept_no"), (e or {}).get("label"),
                   (e or {}).get("text"), fv.get("note"),
                   f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={e['rcept_no']}" if e and e.get("rcept_no") else None]
            for i, v in enumerate(row):
                c = ev.cell(r, 1 + i, v); c.border = BOX; c.alignment = Alignment(wrap_text=True, vertical="top")
            if e and e.get("text"):
                ev.cell(r, 6).fill = MARK
            r += 1
    for k in ("S", "sigma"):
        ev.cell(r, 1, {"S": "주가", "sigma": "변동성"}[k]); ev.cell(r, 2, st["market"].get(k))
        ev.cell(r, 3, st["market"].get("source") or "수동"); r += 1

    rows = st.get("assume") or []
    if rows:
        ws3 = wb.create_sheet("가정 대조")
        ws3["A1"] = "평가보고서 가정 ↔ 공시·대시보드 대조 (✓ 일치 · ✗ 불일치 — 금액 0.5%, 이율 0.01%p 넘게 차이)"
        ws3["A1"].font = Font(bold=True, color=NAVY)
        heads = ["항목", "공시·대시보드", "평가보고서", "판정", "차이", "검토 의견"]
        for ci, h in enumerate(heads, 1):
            c = ws3.cell(3, ci, h); c.fill = HEAD; c.font = Font(bold=True); c.border = BOX
        bad = PatternFill("solid", fgColor="F6E1DF")
        for ri, a in enumerate(rows, 4):
            vals = [a.get("항목"), a.get("공시"), a.get("평가보고서"), a.get("판정"), a.get("차이"),
                    "평가사에 근거 확인 필요" if a.get("판정") == "✗" else ""]
            for ci, v in enumerate(vals, 1):
                c = ws3.cell(ri, ci, v); c.border = BOX
                if a.get("판정") == "✗":
                    c.fill = bad
        for col, w in zip("ABCDEF", (22, 20, 20, 8, 24, 28)):
            ws3.column_dimensions[col].width = w


DW_HEAD = {
    "A": "명칭", "B": "대상회사", "C": "발행회사", "D": "소재시군", "E": "상품", "F": "회차", "G": "평가기준일", "H": "NOTE",
    "I": "평가업체", "J": "접수일", "K": "review일", "L": "투입시간", "M": "Time입력여부", "N": "전체가치", "O": "일반사채",
    "P": "Put", "Q": "전환권", "R": "Call", "S": "@ w/ Call", "T": "@ w/o Call", "U": "RAS_FV", "V": "R사채", "W": "rPut",
    "X": "rCV", "Y": "rCall", "Z": "할인코드", "AA": "기타코드", "AB": "잔여총액(원)", "AC": "발행주수", "AD": "발행액(권)",
    "AE": "발행총액(백만원)", "AF": "발행일", "AG": "만기일", "AH": "표면R", "AI": "액면가(주당)", "AJ": "만기보장R",
    "AK": "연간이자지급횟수", "AL": "상환활증방식", "AM": "RedemR(상수)", "AN": "현조정행사가격", "AO": "최초전환가격",
    "AP": "전환가격조정시작일", "AQ": "전환초일", "AR": "전환말일", "AS": "전환가조정 연회수", "AT": "상하향 조정",
    "AU": "Refix최소(일반or)", "AV": "Put초일", "AW": "배당가능초일", "AX": "계약Put초일", "AY": "Put말일", "AZ": "YTP",
    "BA": "YTP복리", "BB": "Put연회수", "BC": "Call초일", "BD": "Call말일", "BE": "YTC", "BF": "YTC복리", "BG": "Call연회수",
    "BH": "Call한도", "BI": "Call순위", "BJ": "Call3자배정", "DS": "주가", "DT": "변동성", "DU": "신용등급", "EL": "R신용등급",
    "FA": "proxy1", "FB": "proxy2", "FC": "proxy3", "FD": "proxy4", "FE": "proxy5",
    "FM": "pcode1", "FN": "pcode2", "FO": "pcode3", "FP": "pcode4", "FQ": "pcode5",
}
RF_COLS = ["DV", "DW", "DX", "DY", "DZ", "EA", "EB", "EC", "ED", "EE", "EF", "EG", "EH", "EI", "EJ", "EK"]
RD_COLS = ["EM", "EN", "EO", "EP", "EQ", "ER", "ES", "ET", "EU", "EV", "EW", "EX", "EY", "EZ"]
RF_T = ["0.25", "0.5", "0.75", "1", "1.5", "2", "2.5", "3", "4", "5", "7", "10", "15", "20", "30", "50"]
PT_COLS = [get_column_letter(column_index_from_string("BK") + k) for k in range(30)]
CT_COLS = [get_column_letter(column_index_from_string("CO") + k) for k in range(30)]
PT_HEAD = ["Put_Table"] + [f"p{k}" for k in range(2, 31)]
CT_HEAD = ["Call_Table"] + [f"c{k}" for k in range(2, 31)]
FORMULA = {"A", "N", "AC", "AD", "AE", "AV"}
RESULT = {"S", "T", "U", "V", "W", "X", "Y", "Z", "AA"}
AUTO = FORMULA | RESULT


def _sched_table(sched, start, end, per_year):
    """공시 일정표 상환율 → Temp Put_Table/Call_Table (p1 = 초일). Temp가 초일·말일·연회수로 만드는 행사일과 같을 때만 쓴다."""
    if not sched or not start or not per_year:
        return None, None
    import calendar
    step = max(1, int(round(12 / float(per_year))))
    s = _date(start)
    dates = []
    for k in range(0, 31):
        y, mth = divmod(s.month - 1 + step * k, 12)
        d = dt.date(s.year + y, mth + 1, min(s.day, calendar.monthrange(s.year + y, mth + 1)[1]))
        if end and d > _date(end):
            break
        dates.append(d)
    got = [(_date(d), r) for d, r in sched]
    if len(got) != len(dates) or any(abs((a - b).days) > 7 for (a, _), b in zip(got, dates)) or len(got) > 30:
        return None, f"공시 일정표 {len(got)}회가 Temp 행사일({len(dates)}회)과 달라 표를 넣지 않았습니다."
    return [r for _, r in got], None


def _flat16(c):
    xs = [x for x in (c or []) if x not in (None, "")]
    return [xs[0]] * 16 if len(xs) == 1 else (c or [])


def _proxies(row, m):
    """유사기업(비상장 변동성) → Temp proxy1~5(FA~FE)·pcode1~5(FM~FQ). 없으면 상장사 자기 자신."""
    peers = m.get("peers") or []
    if peers:
        for i, pr in enumerate(peers[:5]):
            row[get_column_letter(column_index_from_string("FA") + i)] = pr.get("name")
            row[get_column_letter(column_index_from_string("FM") + i)] = pr.get("code")
    elif m.get("stock_code"):
        row["FA"], row["FM"] = m["company"], m["stock_code"]


def _so_dw_row(st):
    """주식선택권 → Temp DW (상품 'SO'). Temp SO 시트가 쓰는 열: 전환초일·전환말일(AQ·AR) = 행사기간,
    현조정행사가격(AN) = 행사가격, 만기일(AG) = 행사종료일, 주가(DS)·변동성(DT)·Rf(DV~EK)."""
    m, f, rep = st["meta"], st["fields"], st.get("report") or {}
    md = _date(m.get("measure_date") or _v(f, "부여일") or m["val_date"])
    qty = _v(f, "잔여수량") or _v(f, "부여수량")
    k = _v(f, "행사가격")
    per = rep.get("옵션1개당") if rep.get("옵션1개당") is not None else _v(f, "회사FV")
    row = {
        "A": f"{m['company']}SO{m.get('bd_tm') or ''}_{md.strftime('%y%m%d')}",
        "B": m.get("target_company") or m["company"], "C": m["company"], "D": m.get("city"), "E": "SO", "F": m.get("bd_tm"),
        "G": md, "I": m.get("appraiser"), "K": dt.date.today(),
        "N": per * qty if (per is not None and qty) else None,
        "AB": qty * k if (qty and k) else None, "AF": _date(_v(f, "부여일")), "AG": _date(_v(f, "행사종료일")),
        "AH": 0, "AJ": 0, "AK": 1, "AL": "연복리", "AN": k, "AO": k,
        "AQ": _date(_v(f, "행사시작일")), "AR": _date(_v(f, "행사종료일")), "AS": 12, "AT": "일반",
        "DS": st["market"].get("S"), "DT": st["market"].get("sigma"),
    }
    row["AE"], row["AD"], row["AC"] = row["AB"], row["AN"], qty
    for col, v in zip(RF_COLS, _flat16(st["curves"].get("rf"))):
        row[col] = v
    _proxies(row, m)
    row["_notes"] = ["SO: 행사기간 = 전환초일·전환말일(AQ·AR), 행사가격 = 현조정행사가격(AN), 수량 = 발행주수(AC). "
                     "Temp에서 SO 계산으로 확인하세요."]
    return row


def _opt_dw_row(st):
    """풋·콜옵션(주식 대상) → Temp DW (상품 'SO', Temp SO 시트로 계산).
    Temp 규칙: 옵션유형(Input O34) = 명칭(A)에 'Put'이 있으면 Put, 아니면 Call · SO후속(O35) = Put이면 'Y'(Rd 할인), Call이면 'N'(Rf).
    기초자산(Input M35) '주식' = 행사가 고정: 행사가 = 현조정행사가격(AN), 행사기간 = 전환초일·전환말일(AQ·AR).
    기초자산 'CB' = 행사가가 Put(또는 Call) 일정을 따라 커짐: 발행일(AF) = 이율 기산일, 발행액(권)(AD) = 행사가(1주 단위),
    YTP(AZ)·YTC(BE) = 행사가 이율, Put·Call 기간 = 행사기간."""
    m, f, rep = st["meta"], st["fields"], st.get("report") or {}
    vd = _date(m["val_date"])
    typ = _v(f, "옵션종류") or "Put"
    is_put = typ != "Call"
    k, qty = _v(f, "행사가격"), _v(f, "수량")
    rate, comp = _v(f, "행사가증가율") or 0, _v(f, "행사가복리") or "연복리"
    s, e = _date(_v(f, "행사시작일")) or vd, _date(_v(f, "행사종료일"))
    base = _date(_v(f, "행사가기산일")) or vd
    per = rep.get("옵션1개당")
    row = {
        "A": f"{m['company']}{typ}{m.get('bd_tm') or ''}_{vd.strftime('%y%m%d')}",
        "B": m.get("target_company") or m["company"], "C": m["company"], "D": m.get("city"), "E": "SO", "F": m.get("bd_tm"),
        "G": vd, "I": m.get("appraiser"), "K": dt.date.today(),
        "N": per * qty if (per is not None and qty) else None,
        "AB": qty * k if (qty and k) else None, "AF": base, "AG": e,
        "AH": 0, "AJ": 0, "AK": 1, "AL": "연복리", "AN": k, "AO": k,
        "AQ": s, "AR": e, "AS": 12, "AT": "일반",
        "DS": st["market"].get("S"), "DT": st["market"].get("sigma"), "DU": m.get("credit_grade"), "EL": m.get("credit_grade"),
    }
    row["AE"], row["AD"], row["AC"] = row["AB"], k, qty
    notes = [f"{typ} 옵션: 명칭(A)에 '{typ}'를 넣었습니다 — Temp는 명칭으로 옵션유형을 정합니다 (Input O34)."
             + (" Put이라 Temp는 Rd로 할인합니다 (SO후속=Y)." if is_put else " Call이라 Temp는 Rf로 할인합니다 (SO후속=N).")]
    if rate:
        if is_put:
            row.update({"AX": s, "AY": e, "AZ": rate, "BA": comp, "BB": 12, "AV": s})
        else:
            row.update({"BC": s, "BD": e, "BE": rate, "BF": comp, "BG": 12, "BH": 1, "BI": "선순위", "BJ": "N"})
        notes.append(f"행사가에 연 {rate:.2%}({comp})가 붙습니다 → Temp Input!M35(so기초자산)를 'CB'로 두세요: "
                     f"{'Put' if is_put else 'Call'} 일정(월 1회, 기산일 = 발행일 AF)이 행사가가 됩니다. "
                     "대시보드는 행사기간 안 매 단계 행사를 허용하므로 Temp(일정일에만 행사)보다 조금 클 수 있습니다.")
    else:
        notes.append("행사가 고정 → Temp Input!M35(so기초자산)를 '주식'으로 두세요 (행사가 = AN, 행사기간 = AQ·AR).")
    for col, v in zip(RF_COLS, _flat16(st["curves"].get("rf"))):
        row[col] = v
    for col, v in zip(RD_COLS, _flat16(st["curves"].get("rd"))[:14]):
        row[col] = v
    _proxies(row, m)
    row["_notes"] = notes
    return row


def dw_row(st):
    m, f, rep = st["meta"], st["fields"], st.get("report") or {}
    if (m.get("kind") or "CB") == "SO":
        return _so_dw_row(st)
    if (m.get("kind") or "CB") == "OPT":
        return _opt_dw_row(st)
    vd = _date(m["val_date"])
    put_first = _v(f, "Put초일")
    o, p_, q = rep.get("일반사채"), rep.get("Put"), rep.get("전환권")
    row = {
        "A": f"{m['company']}{m.get('kind') or 'CB'}{m.get('bd_tm') or ''}_{vd.strftime('%y%m%d')}",
        "B": m.get("target_company") or m["company"], "C": m["company"], "D": m.get("city"),
        "E": m.get("kind") or "CB", "F": m.get("bd_tm"),
        "G": vd, "I": m.get("appraiser"), "K": dt.date.today(),
        "N": sum(x for x in (o, p_, q) if x is not None) if any(x is not None for x in (o, p_, q)) else None,
        "O": o, "P": p_, "Q": q, "R": rep.get("Call"),
        "AB": _v(f, "잔여총액"), "AF": _date(_v(f, "발행일")), "AG": _date(_v(f, "만기일")),
        "AH": _v(f, "표면이자율") or 0, "AI": m.get("par_value"), "AJ": _v(f, "만기이자율") or 0,
        "AK": _v(f, "연간이자지급횟수") or 1, "AL": _v(f, "상환활증방식") or "연복리",
        "AN": _v(f, "현조정행사가격"), "AO": _v(f, "최초전환가격"), "AP": _date(_v(f, "전환가격조정시작일")),
        "AQ": _date(_v(f, "전환초일")), "AR": _date(_v(f, "전환말일")), "AS": _v(f, "전환가조정연회수") or 12,
        "AT": _v(f, "상하향조정") or "하", "AU": _v(f, "Refix최소"),
        "AX": _date(put_first), "AY": _date(_v(f, "Put말일")), "AZ": _v(f, "YTP") or 0, "BA": _v(f, "YTP복리") or "연복리",
        "BB": _v(f, "Put연회수") or 12, "BC": _date(_v(f, "Call초일")), "BD": _date(_v(f, "Call말일")),
        "BE": _v(f, "YTC") or 0, "BF": _v(f, "YTC복리") or "연복리", "BG": _v(f, "Call연회수") or 12,
        "BH": _v(f, "Call한도") if _v(f, "Call초일") else None, "BI": _v(f, "Call순위") or "선순위",
        "BJ": _v(f, "Call3자배정") or "N",
        "DS": st["market"].get("S"), "DT": st["market"].get("sigma"), "DU": m.get("credit_grade"), "EL": m.get("credit_grade"),
    }
    _proxies(row, m)
    if (_v(f, "상환활증방식") == "고정율") and _v(f, "RedemR"):
        row["AM"] = _v(f, "RedemR")
    row["AE"] = row["AB"]
    row["AD"] = row["AN"]
    row["AC"] = (row["AE"] / row["AD"]) if row["AE"] and row["AD"] else None
    row["AV"] = row["AX"]
    if (m.get("kind") or "CB") in ("RCPS", "CPS"):
        row["AI"] = _v(f, "액면가") or m.get("par_value")
        row["AW"] = _date(_v(f, "배당가능초일"))
        if row["AW"] and row["AX"]:
            row["AV"] = max(row["AW"], row["AX"])
        if _v(f, "상하향조정") == "공모가조정" and _v(f, "공모가하한"):
            row["AU"] = f"공모가*{round(_v(f, '공모가하한') * 100)}%"
    notes = []
    vd_iso = m["val_date"]
    if _v(f, "Call말일") and _v(f, "Call말일") < vd_iso:
        for c in ("BC", "BD", "BE", "BF", "BG", "BH", "BI", "BJ"):
            row[c] = None
    elif m.get("use_tables", True):
        tbl, note = _sched_table(_v(f, "Call일정"), row["BC"], row["BD"], row["BG"])
        if tbl:
            row.update(dict(zip(CT_COLS, tbl)))
        if note:
            notes.append("Call " + note)
    if _v(f, "Put말일") and _v(f, "Put말일") < vd_iso:
        for c in ("AX", "AY", "AZ", "BA", "BB", "AV", "AW"):
            row[c] = None
    elif m.get("use_tables", True):
        tbl, note = _sched_table(_v(f, "Put일정"), row["AX"], row["AY"], row["BB"])
        if tbl:
            row.update(dict(zip(PT_COLS, tbl)))
        if note:
            notes.append("Put " + note)
    for col, v in zip(RF_COLS, _flat16(st["curves"]["rf"])):
        row[col] = v
    for col, v in zip(RD_COLS, _flat16(st["curves"]["rd"])[:14]):
        row[col] = v
    row["_notes"] = notes
    return row


def headers():
    h = dict(DW_HEAD)
    h.update({c: f"Rf_{t}" for c, t in zip(RF_COLS, RF_T)})
    h.update({c: f"Rd_{t}" for c, t in zip(RD_COLS, RF_T)})
    h.update(dict(zip(PT_COLS, PT_HEAD)))
    h.update(dict(zip(CT_COLS, CT_HEAD)))
    return h




def dw_xlsx(st):
    row = dw_row(st)
    wb = Workbook()
    ws = wb.active
    ws.title = "DW_붙여넣기"
    heads = headers()
    last = column_index_from_string("FZ")
    ws["A1"] = ("Temp_v530 DW 시트 붙여넣기용. 3행을 복사 → DW 시트 빈 행의 A열 선택 → 선택하여 붙여넣기: '값' + '내용 있는 셀만 붙여넣기'(Skip blanks). "
                "회색 열은 Temp 표의 수식 열(명칭·전체가치·발행주수·발행액·발행총액·Put초일)과 Temp 계산 결과 열이라 비워 두었습니다.")
    ws["A1"].font = Font(bold=True, color=ACC)
    for ci in range(1, last + 1):
        col = get_column_letter(ci)
        h = ws.cell(2, ci, heads.get(col))
        h.fill = HEAD; h.font = Font(bold=True); h.border = BOX
        v = None if col in AUTO else row.get(col)
        c = ws.cell(3, ci, v)
        c.border = BOX
        if isinstance(v, dt.date):
            c.number_format = "yyyy-mm-dd"
        if col in AUTO:
            h.fill = PatternFill("solid", fgColor="D3CEC4"); c.fill = PatternFill("solid", fgColor="EDE9E1")
        ws.column_dimensions[col].width = 13
    ws.freeze_panes = "B3"
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()
