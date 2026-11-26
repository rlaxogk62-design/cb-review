"""CB·EB·BW 이항모형 (쓰베리오티스-페르난데스, TF 방식).

- 주식 부분(전환으로 받는 몫)은 무위험 선도금리, 채권 부분(현금으로 받는 몫)은 위험 선도금리(Rd 곡선)로 할인한다.
- 금리 곡선(연 %)은 3개월 이표 par YTM으로 보고 부트스트랩해 zero 금리를 만든다 (curve_kind='par', 기본값).
  실무 검증에서 일반사채·Put이 Temp_v530과 0.05% 이내로 일치한 방식이다.
- 분해 순서는 리뷰 양식과 같다: 일반사채 → Put → 전환권 → Call
    Put    = V(사채+Put) − V(사채)
    전환권 = V(사채+Put+전환) − V(사채+Put)
    Call   = V(전체) − V(사채+Put+전환)        (보유자 입장에서 음수)
- Call(매도청구권)은 Temp_v530 시트 수식을 그대로 따른다 (call_mode='temp', 기본값).
    CB 노드   = MIN(Call가격, MAX(전환가치, Put+쿠폰, 보유가치))   ← Call 행사일에만 MIN
    선순위 Call(CVonCall='N')이면 Call 말일까지 전환 불가 (CrS 시트 '전환onCall')
    rCall    = Call한도 × (V(Call 있음) − V(Call 없음))           ← Input 시트 간접법 (V24 = V21 − W21)
- 격자: Δt = 1/연간step 고정, 단계 수 = ROUND(잔존일수/365 × 연간step) (grid='temp', 기본값)
- Put·Call 상환율: 공시 표의 상환율이 있으면 그대로 쓰고, 없으면 Temp처럼 연복리 (1+y)^경과연수 로 계산해 소수 6자리에서 버린다.
- 리픽싱: Temp X 시트(경로확률반영X) — 조정일 아닌 노드는 두 부모 전환가의 경로 수 가중평균, 조정일 노드는
  하향: MAX(MIN(직전 전환가, 노드 주가), 하한) / 상하: 노드 주가를 [하한, 최초 전환가]로 자른 값. 전환주식수 = 권면/전환가.

검증: 일반사채·Put은 Temp와 0.00%, Call은 0.3% 이내, 전환권은 Put만 있는 두 사례에서 0.5% 이내 (사내 README 참고).
"""
import calendar
import math
import datetime as dt

RF_TENORS = [0.25, 0.5, 0.75, 1, 1.5, 2, 2.5, 3, 4, 5, 7, 10, 15, 20, 30, 50]
RD_TENORS = [0.25, 0.5, 0.75, 1, 1.5, 2, 2.5, 3, 4, 5, 7, 10, 15, 20]


def _d(s):
    return dt.date.fromisoformat(s) if isinstance(s, str) else s


def yf(a, b):
    return (_d(b) - _d(a)).days / 365.0


def add_months(d, months):
    d = _d(d)
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def years_between(a, b):
    """월 단위로 딱 떨어지면 개월수/12, 아니면 실제일수/365."""
    a, b = _d(a), _d(b)
    months = (b.year - a.year) * 12 + (b.month - a.month)
    if add_months(a, months) == b:
        return months / 12.0
    return yf(a, b)


def schedule(start, end, per_year):
    if not start or not per_year:
        return []
    step = max(1, int(round(12 / float(per_year))))
    out, end = [], (_d(end) if end else None)
    for k in range(0, 600):
        d = add_months(start, step * k)
        if end and d > end:
            break
        out.append(d)
        if not end:
            break
    return out


def _interp(pts, t):
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, r0), (t1, r1) in zip(pts, pts[1:]):
        if t <= t1:
            return r0 + (r1 - r0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


def curve(tenors, rates_pct, kind="par"):
    """연 % 곡선 → 연속복리 zero 금리 함수 z(t).
    kind='zero': 입력을 연복리 zero로 본다.
    kind='par' : 입력을 3개월 이표 par YTM으로 보고 부트스트랩한다 (장기 구간이 깨지면 마지막 zero로 평탄 연장)."""
    pts = [(t, float(r)) for t, r in zip(tenors, rates_pct) if r is not None and str(r).strip() != ""]
    if not pts:
        raise ValueError("금리 곡선이 비어 있습니다.")
    if kind == "par":
        zp, dfs, acc = [], [], 0.0
        for k in range(1, int(pts[-1][0] * 4) + 1):
            t = k / 4
            y = _interp(pts, t) / 100
            df = (1 - y / 4 * acc) / (1 + y / 4)
            if df <= 0 or (dfs and df >= dfs[-1]):
                break
            dfs.append(df)
            acc += df
            zp.append((t, -math.log(df) / t))
    else:
        zp = [(t, math.log(1 + r / 100)) for t, r in pts]
    return lambda t: _interp(zp, t)


def trunc6(x):
    return math.floor(x * 1e6 + 1e-9) / 1e6


COMP_M = {"분복리": 4, "반복리": 2, "월복리": 12}


def ratio_for(y, comp, tau, m=1):
    """발행 후 tau년에 받는 상환율(원금 대비). comp: 연복리·분복리(3개월)·단리 (Temp 목록). m: 연복리일 때 연 복리 횟수."""
    if tau <= 0:
        return 1.0
    if comp == "단리":
        return 1 + y * tau
    m = COMP_M.get(comp, max(1.0, float(m or 1)))
    return (1 + y / m) ** (m * tau)


def coupon_dates(issue, mat, cf):
    return schedule(add_months(issue, int(round(12 / cf))), mat, cf) if cf else []


def accreted(F, ratio, tau, c, cf, y, comp, m):
    """상환율 ratio로 받는 금액에서 이미 받은 표면이자(만기보장수익률로 불린 값)를 뺀다."""
    R = F * ratio
    if c > 0 and cf:
        paid = math.floor(tau * cf + 1e-9)
        for k in range(1, paid + 1):
            R -= F * c / cf * ratio_for(y, comp, tau - k / cf, m)
    return R


def xround(x):
    """엑셀 ROUND(x, 0) — 0.5는 0에서 먼 쪽으로."""
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def datedif_m(a, b):
    """엑셀 DATEDIF(a, b, "m"): 꽉 찬 개월 수."""
    a, b = _d(a), _d(b)
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


def refix_schedule(p, N, spy, val):
    """조정초step과 단계별 조정일 여부. Temp Input!C31·C33, X!D6~D8·F7·17~18행을 그대로 따른다.
    전환가격조정시작일이 비어 있으면 Temp처럼 엑셀 날짜 0(1900-01-00 = 1899-12-31)을 기준으로 삼는다."""
    freq = float(p.get("refix_freq") or 12)
    m_step = 12.0 / freq
    anchor = _d(p["refix_start"]) if p.get("refix_start") else dt.date(1899, 12, 31)
    if val >= anchor:
        k = math.ceil((datedif_m(anchor, val) + 1) / m_step - 1e-12) * m_step
        first = add_months(anchor, int(round(k)))
    else:
        first = anchor
    s0 = max(xround((first - val).days / 365 * spy), 1) + int(p.get("refix_adjust") or 0)
    interval = round(spy / freq, 1)
    d7 = 0 if abs(s0 - interval) < 1e-9 else s0
    e6 = math.floor(interval)

    def raw(n):
        return math.floor(math.fmod(n, interval) + 1e-9)

    def mod(n):
        return d7 if (raw(n + 1) == 0 and d7 == e6) else raw(n)

    target = mod(s0)
    adj = [n >= s0 and mod(n) == target for n in range(N + 1)]
    return s0, adj, first


def x_lattice(p, N, S0, u, adj, s0):
    """노드별 전환가 X (경로확률반영X).
    조정일 아님: 두 부모의 경로 수(COMBIN) 가중평균 = (j·X위 + (n−j)·X아래)/n
    조정일, 하향: MAX(IF(S<하한, 하한, MIN(직전X, S)), 하한)   직전X = 위쪽 부모(없으면 아래쪽)
    조정일, 상하: S를 [하한, 최초전환가] 안으로 자른 값"""
    x0 = float(p["conv_price"])
    floor = float(p["refix_floor"])
    down_only = (p.get("refix_type") or "하") == "하"
    cap = x0 if down_only else float(p.get("first_conv_price") or x0)
    X = [[x0]]
    for n in range(1, N + 1):
        prev = X[-1]
        row = [x0] * (n + 1)
        if n >= s0:
            for j in range(n + 1):
                if adj[n]:
                    s = S0 * u ** (2 * j - n)
                    if down_only:
                        px = prev[j - 1] if j >= 1 else prev[0]
                        x = floor if s < floor else min(px, s)
                    else:
                        x = cap if s >= cap else (floor if s <= floor else s)
                else:
                    x = ((j * prev[j - 1] if j >= 1 else 0.0) + ((n - j) * prev[j] if j <= n - 1 else 0.0)) / n
                row[j] = max(x, floor) if down_only else x
        X.append(row)
    return X


def refix_on(p):
    """Temp Input!Q18: 상하향이 '일반'이 아니고 '공모'가 들어가지 않으며 최저행사가가 숫자일 때만 리픽싱."""
    t = str(p.get("refix_type") or "일반")
    try:
        fl = float(p.get("refix_floor"))
    except (TypeError, ValueError):
        return False
    return t not in ("일반", "") and "공모" not in t and fl > 0


def lattice(p, use_put, use_conv, use_call, record=None):
    val, mat, issue = _d(p["val_date"]), _d(p["maturity"]), _d(p["issue_date"])
    T = yf(val, mat)
    if T <= 0:
        raise ValueError("평가기준일이 만기일 이후입니다.")
    spy = float(p.get("steps_per_year") or 52)
    N = max(8, int(round(T * spy)))
    temp_grid = p.get("grid", "temp") == "temp"
    dtt = 1.0 / spy if temp_grid else T / N
    zf, zd = p["_zf"], p["_zd"]

    def fwd(z, i):
        t0, t1 = i * dtt, (i + 1) * dtt
        return (z(t1) * t1 - (z(t0) * t0 if i else 0.0)) / dtt

    rf = [fwd(zf, i) for i in range(N)]
    rd = [fwd(zd, i) for i in range(N)]
    sig = float(p["sigma"])
    if temp_grid:
        u = round(math.exp(round(sig * math.sqrt(dtt), 5)), 5)
    else:
        u = math.exp(sig * math.sqrt(dtt))
    dn = 1 / u
    F = float(p["face"])
    c, cf = float(p.get("coupon") or 0), float(p.get("coupon_freq") or 1)
    CR = F / float(p["conv_price"])
    S0 = float(p["S"])

    def idx(d):
        t = yf(val, d)
        if t < -1e-9 or t > T + 1e-9:
            return None
        return min(N, max(0, int(round(t * spy)) if temp_grid else int(round(t / dtt))))

    coupon = [0.0] * (N + 1)
    if c > 0:
        for d in coupon_dates(issue, mat, cf):
            i = idx(d)
            if i is not None and d > val:
                coupon[i] += F * c / cf

    def exercise_prices(kind):
        sched = p.get(f"{kind}_schedule")
        y = float(p.get("ytp" if kind == "put" else "ytc") or 0)
        comp = p.get("ytp_comp" if kind == "put" else "ytc_comp") or "연복리"
        m = float(p.get(f"{kind}_freq") or 12)
        if sched:
            dates = [(_d(d), r) for d, r in sched]
        else:
            if not p.get(f"{kind}_start"):
                return {}
            tbl = p.get(f"{kind}_table") or []
            end = p.get(f"{kind}_end") or (mat if kind == "put" else p[f"{kind}_start"])
            ds = schedule(p[f"{kind}_start"], end, m)
            dates = [(d, tbl[k] if k < len(tbl) and tbl[k] else None) for k, d in enumerate(ds)]
        out = {}
        for d, r in dates:
            i = idx(d)
            if i is None or d < val:
                continue
            tau = years_between(issue, d)
            if r:
                out[i] = F * float(r) + coupon[i]
            else:
                ratio = trunc6(ratio_for(y, comp, tau, 1))
                out[i] = accreted(F, ratio, tau, c, cf, y, comp, 1) + coupon[i]
        return out

    put_px = exercise_prices("put") if use_put else {}
    call_px = exercise_prices("call")

    conv_from = idx(max(_d(p.get("conv_start") or issue), val)) if use_conv else None
    conv_to = idx(min(_d(p.get("conv_end") or mat), mat)) if use_conv else None
    if use_conv and conv_from is None:
        conv_from = 0

    Tm = years_between(issue, mat)
    if p.get("ytm_comp") == "고정율" and p.get("redem_ratio"):
        rm = float(p["redem_ratio"])
        Rm = F * rm + coupon[N]
    else:
        rm = ratio_for(float(p.get("ytm") or 0), p.get("ytm_comp") or "연복리", Tm, 1)
        Rm = accreted(F, rm, Tm, c, cf, float(p.get("ytm") or 0), p.get("ytm_comp") or "연복리", 1) + coupon[N]

    block_to = -1
    if use_call and call_px and p.get("cv_on_call", "N") == "N":
        block_to = max(call_px)

    def conv_ok(i):
        return (use_conv and conv_from is not None and conv_from <= i <= (conv_to if conv_to is not None else N)
                and i > block_to)

    Xl = None
    if use_conv and refix_on(p):
        key = ("_xl", N, round(u, 12), round(S0, 6))
        if p.get("_xcache", {}).get("key") != key:
            s0, adj, first = refix_schedule(p, N, spy, val)
            p["_xcache"] = {"key": key, "X": x_lattice(p, N, S0, u, adj, s0), "s0": s0, "first": first,
                            "n_adj": sum(adj)}
        Xl = p["_xcache"]["X"]

    def cvalue(i, j):
        s = S0 * u ** (2 * j - i)
        return (F / Xl[i][j]) * s if Xl is not None else CR * s

    E, D = [0.0] * (N + 1), [0.0] * (N + 1)
    for j in range(N + 1):
        cv = cvalue(N, j) if conv_ok(N) else 0.0
        if cv > Rm:
            E[j], D[j] = cv, 0.0
        else:
            E[j], D[j] = 0.0, Rm
    if record is not None and N in record:
        record[N] = [E[j] + D[j] for j in range(N + 1)]

    mode = p.get("call_mode") or "temp"
    for i in range(N - 1, -1, -1):
        pu = (math.exp(rf[i] * dtt) - dn) / (u - dn)
        if not 0 < pu < 1:
            raise ValueError("격자 확률이 0~1을 벗어났습니다. 연 Step 수를 늘리거나 변동성을 확인하세요.")
        pd = 1 - pu
        dfr, dfd = math.exp(-rf[i] * dtt), math.exp(-rd[i] * dtt)
        can_conv = conv_ok(i)
        cp = call_px.get(i) if use_call else None
        pp = put_px.get(i)
        for j in range(i + 1):
            e = dfr * (pu * E[j + 1] + pd * E[j])
            d_ = dfd * (pu * D[j + 1] + pd * D[j]) + coupon[i]
            hold = e + d_
            cv = cvalue(i, j) if can_conv else 0.0
            if mode == "temp" or cp is None:
                v = max(cv, pp or 0.0, hold)
                if cp is not None:
                    v = min(cp, v)
                if can_conv and v == cv and cv > 0:
                    e, d_ = cv, 0.0
                elif pp is not None and v == pp:
                    e, d_ = 0.0, pp
                elif cp is not None and v == cp:
                    e, d_ = 0.0, cp
            elif mode == "embedded_escape":
                v = hold
                if v > max(cp, cv):
                    e, d_ = (cv, 0.0) if cv >= cp else (0.0, cp)
                    v = e + d_
                if pp is not None and pp > v:
                    e, d_, v = 0.0, pp, pp
                if can_conv and cv > v:
                    e, d_, v = cv, 0.0, cv
            else:
                v = hold
                if v > cp:
                    e, d_, v, cv = 0.0, cp, cp, 0.0
                if pp is not None and pp > v:
                    e, d_, v = 0.0, pp, pp
                if can_conv and cv > v:
                    e, d_, v = cv, 0.0, cv
            E[j], D[j] = e, d_
        if record is not None and i in record:
            record[i] = [E[j] + D[j] for j in range(i + 1)]
    ctx = {"N": N, "dt": dtt, "rf": rf, "rd": rd, "u": u, "call_px": call_px, "put_px": put_px}
    info = {"N": N, "T": T, "rf_T": math.exp(zf(T)) - 1, "rd_T": math.exp(zd(T)) - 1,
            "put_dates": len(put_px), "call_dates": len(call_px), "redem_ratio": rm,
            "refix": bool(Xl is not None)}
    if Xl is not None:
        xc = p["_xcache"]
        info.update({"refix_first": xc["first"].isoformat(), "refix_s0": xc["s0"], "refix_nodes": xc["n_adj"]})
    return E[0] + D[0], info, ctx


def _call_option(ctx, rec, rates):
    """기록해 둔 CB 가치에 대해, 매매가액으로 사는 미국형 옵션의 가치."""
    u, dtt = ctx["u"], ctx["dt"]
    dn = 1 / u
    O = None
    for i in range(max(rec), -1, -1):
        if O is not None:
            pu = (math.exp(ctx["rf"][i] * dtt) - dn) / (u - dn)
            df = math.exp(-rates[i] * dtt)
            O = [df * (pu * O[j + 1] + (1 - pu) * O[j]) for j in range(i + 1)]
        if i in rec:
            ex = [max(v - ctx["call_px"][i], 0.0) for v in rec[i]]
            O = ex if O is None else [max(a, b) for a, b in zip(O, ex)]
    return O[0]


def value(p):
    p = dict(p)
    p["_zf"] = curve(RF_TENORS, p["rf_curve"], p.get("rf_kind") or p.get("curve_kind") or "par")
    p["_zd"] = curve(RD_TENORS, p["rd_curve"], p.get("curve_kind") or "par")
    has_put = bool(p.get("put_start") or p.get("put_schedule"))
    h = float(p.get("call_ratio") or 0)
    has_call = bool(p.get("call_start") or p.get("call_schedule")) and h > 0
    mode = p.get("call_mode") or "temp"
    if p.get("cv_on_call") is None:
        p["cv_on_call"] = "N" if (p.get("call_rank") or "선순위") == "선순위" else "Y"

    bond, _, _ = lattice(p, False, False, False)
    bp, _, ctx = lattice(p, has_put, False, False)
    rec = {i: None for i in ctx["call_px"]} if (has_call and mode.startswith("option")) else None
    bpc, info, ctx2 = lattice(p, has_put, True, False, record=rec)
    if has_call and not ctx["call_px"]:
        has_call = False
    if has_call and mode.startswith("option"):
        full = bpc - h * _call_option(ctx2, rec, ctx2["rd"] if mode == "option_rd" else ctx2["rf"])
    elif has_call:
        full = bpc + h * (lattice(p, has_put, True, True)[0] - bpc)
    else:
        full = bpc
    res = {"전체가치": full, "일반사채": bond, "Put": bp - bond, "전환권": bpc - bp, "Call": full - bpc}
    res["파생상품 합계"] = res["Put"] + res["전환권"] + res["Call"]
    info["call_mode"] = mode
    return {"result": res, "info": info}


def implied_conv_at_issue(res, issue_amount):
    """발행일 평가(전체FV = 발행금액 기준): 전환권을 끼워맞춤 값으로 바꾼다 (리뷰 양식 1.2)."""
    r = dict(res)
    r["전환권"] = issue_amount - r["일반사채"] - r["Put"] - r["Call"]
    r["전체가치"] = issue_amount
    r["파생상품 합계"] = r["Put"] + r["전환권"] + r["Call"]
    return r
