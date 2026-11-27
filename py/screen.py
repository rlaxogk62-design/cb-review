"""딜 스크리닝 — 시장 전체의 CB·BW·EB(·RCPS) 발행 공시를 모아 회차별로 평가한다.
  - 평가기준일 이론가치 (권면 100 기준) = 같은 엔진(Temp 규칙 TF 이항) · 같은 자동 입력(발행조건 + 조정·잔액 이력)
  - 발행시점 이론가치 (발행가 100 기준) = 발행일 주가·변동성·곡선으로 다시 계산 → 100보다 크면 투자자에게 싸게 발행된 것
  - 내재 스프레드 = 발행시점 이론가치가 발행가와 같아지는 Rd − Rf (발행 조건이 시장에서 받아들여진 신용 스프레드)
곡선: 사내는 법인 RAS 서버(국고채·등급 곡선), 외부 웹은 화면에서 붙여넣은 Rf·Rd를 모든 회차에 쓴다."""
import copy
import datetime as dt
import re
import time

import dart
import engine
import market
import notes

_MEM = {}


def _memo(key, fn, ttl=1800):
    hit = _MEM.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _MEM[key] = (time.time(), v)
    return v


KINDS = [("CB", r"전환사채권?\s*발행\s*결정"), ("BW", r"신주인수권부\s*사채권?\s*발행\s*결정"), ("EB", r"교환사채권?\s*발행\s*결정"),
         ("RCPS", r"유상\s*증자\s*결정")]


def universe(since, until=None, kinds=("CB", "BW", "EB")):
    """기간 안 발행결정 공시(최종본)를 낸 상장사 목록. DART는 회사를 안 정하면 한 번에 3개월까지만 조회된다."""
    until = until or dt.date.today().isoformat()
    a, b = dt.date.fromisoformat(since), dt.date.fromisoformat(until)
    pats = [(k, p) for k, p in KINDS if k in kinds]
    corps = {}
    s = a
    while s <= b:
        e = min(b, s + dt.timedelta(days=89))
        page = 1
        while page <= 60:
            j = _memo(("list", s, e, page), lambda: dart._json("list.json", bgn_de=s.strftime("%Y%m%d"), end_de=e.strftime("%Y%m%d"),
                                                               pblntf_ty="B", last_reprt_at="Y", page_no=page, page_count=100), ttl=3600)
            for d in j.get("list", []) or []:
                nm = d["report_nm"]
                if d.get("corp_cls") not in ("Y", "K") or re.search(r"종속회사|자회사", nm):
                    continue
                kind = next((k for k, p in pats if re.search(p, nm)), None)
                if not kind:
                    continue
                c = corps.setdefault(d["corp_code"], {"corp_code": d["corp_code"], "corp_name": d["corp_name"],
                                                     "stock_code": d.get("stock_code"), "corp_cls": d["corp_cls"], "filings": []})
                c["filings"].append({"rcept_no": d["rcept_no"], "date": dart._iso(d["rcept_dt"]), "kind": kind, "report_nm": nm.strip()})
            if page >= int(j.get("total_page", 1) or 1):
                break
            page += 1
        s = e + dt.timedelta(days=1)
    out = sorted(corps.values(), key=lambda c: max(f["date"] for f in c["filings"]), reverse=True)
    return {"since": since, "until": until, "corps": out, "n_filings": sum(len(c["filings"]) for c in out)}


class Curves:
    """평가일별 Rf·Rd 곡선. ras가 있으면 법인 서버(등급 곡선), 없으면 화면에서 준 곡선을 모든 날짜에 쓴다."""

    def __init__(self, ras=None, rf=None, rd=None):
        self.ras, self.rf, self.rd = ras, rf, rd

    def get(self, date, grade):
        if self.ras is not None:
            return _memo(("curve", date, grade), lambda: self.ras.curves(date, grade), ttl=6 * 3600)
        if not self.rf or not self.rd:
            raise ValueError("Rf·Rd 곡선을 붙여넣어 주세요 (외부 웹은 모든 회차에 같은 곡선을 씁니다).")
        return {"rf": self.rf, "rd": self.rd, "rf_date": "입력값", "grade": grade or "입력 곡선"}


def _stock(ras, code, date, cls):
    return _memo(("stock", code, date), lambda: market.stock(code, date, cls, ras=ras), ttl=6 * 3600)


def _grade(ras, corp, date, private, mode, fallback):
    """곡선 등급코드와 출처. mode 'nice' = 회사별 나이스 등급(사내), 아니면 일괄 가정."""
    fb = fallback if private else "공모부" + fallback
    if mode != "nice" or ras is None:
        return fb, f"일괄 가정 {fb}"
    try:
        co = _memo(("company", corp["corp_code"]), lambda: dart.company(corp["corp_code"]), ttl=86400)
        city = market.city_from_address(co.get("adres"))
        n = _memo(("nice", corp["corp_name"], city), lambda: ras.nice_credit(corp["corp_name"], city), ttl=6 * 3600)
        g = market.grade_asof(n["rows"], date)
        if g and g.get("신용등급"):
            code = market.curve_code(g["신용등급"], private)
            return code, f"나이스 {g['신용등급']} ({g.get('평가일자')})"
        return fb, "나이스 수집 중 → " + fb if n.get("collecting") else "나이스 등급 없음 → " + fb
    except Exception as ex:
        return fb, f"나이스 조회 실패({str(ex)[:30]}) → {fb}"


def _par(tenors, vals, t):
    pts = [(T, float(v)) for T, v in zip(tenors, vals) if v not in (None, "")]
    return engine._interp(pts, t) if pts else None


def rd_from_spread(rf, s):
    """Rd(14칸) = Rf par + s (연 %p, 소수 s=0.05 → 5%p)."""
    return [round(_par(engine.RF_TENORS, rf, t) + s * 100, 6) for t in engine.RD_TENORS]


LONG_T = 10


def _spy(fields, val_date):
    mat = (fields.get("만기일") or {}).get("value")
    try:
        return 12 if mat and engine.yf(dt.date.fromisoformat(val_date), dt.date.fromisoformat(mat)) > LONG_T else 52
    except Exception:
        return 52


def _state(fields, val_date, kind, S, sigma, rf, rd):
    return {"meta": {"val_date": val_date, "steps_per_year": _spy(fields, val_date), "use_tables": True, "kind": kind},
            "fields": fields, "market": {"S": S, "sigma": sigma}, "curves": {"rf": rf, "rd": rd}}


def value_state(st):
    from api import engine_params
    return engine.value(engine_params(st))["result"]


def implied_spread(fields, val_date, kind, S, sigma, rf, target, lo=-0.03, hi=0.6):
    """value(Rd = Rf + s) = target 인 s (Illinois 할선법). 값이 s에 둔감하면(주식가치가 대부분) None."""
    def f(s):
        return value_state(_state(fields, val_date, kind, S, sigma, rf, rd_from_spread(rf, s)))["전체가치"] - target

    flo, fhi = f(lo), f(hi)
    if abs(flo - fhi) < target * 1e-3:
        return None, "이론가치가 할인율에 거의 안 움직임 (즉시 Put·짧은 잔존 등) — 역산 의미 없음"
    if abs(flo) < target * 1e-4:
        return None, "Call 가격 등에 막혀 낮은 스프레드에서 이론가치가 발행가로 고정 — 내재 스프레드가 하나로 정해지지 않음"
    if flo < 0:
        return None, f"Rd = Rf{lo * 100:+.0f}%p로도 발행가에 못 미침 (발행가가 이론가치보다 높음)"
    if fhi > 0:
        return None, f"Rd = Rf+{hi * 100:.0f}%p로도 발행가를 넘음 (전환권 가치가 커서 신용 스프레드로 맞출 수 없음)"
    side = 0
    for _ in range(40):
        s = (lo * fhi - hi * flo) / (fhi - flo)
        fs = f(s)
        if abs(fs) <= target * 1e-6 or hi - lo < 1e-5:
            return s, None
        if fs > 0:
            lo, flo = s, fs
            if side == 1:
                fhi /= 2
            side = 1
        else:
            hi, fhi = s, fs
            if side == -1:
                flo /= 2
            side = -1
    return (lo + hi) / 2, None


def _solve(fn, lo, hi, tol):
    """fn(lo)·fn(hi) 부호가 다를 때 fn(s) = 0 인 s (Illinois 할선법). 아니면 None."""
    flo, fhi = fn(lo), fn(hi)
    if flo == 0:
        return lo
    if flo * fhi > 0:
        return None
    side = 0
    for _ in range(40):
        s = (lo * fhi - hi * flo) / (fhi - flo)
        fs = fn(s)
        if abs(fs) <= tol or hi - lo < 1e-5:
            return s
        if (fs > 0) == (fhi > 0):
            hi, fhi = s, fs
            if side == -1:
                flo /= 2
            side = -1
        else:
            lo, flo = s, fs
            if side == 1:
                fhi /= 2
            side = 1
    return (lo + hi) / 2


def first_root(fn, grid, tol):
    """격자에서 처음 부호가 바뀌는 구간의 근. 파생 = 전체 − 일반사채는 스프레드에 대해 오르다 내려가므로
    (높은 스프레드에서는 Put·전환 하한에 막힘) 낮은 쪽 근을 고른다."""
    vals = [(s, fn(s)) for s in grid]
    for (a, fa), (b, fb) in zip(vals, vals[1:]):
        if fa == 0:
            return a
        if fa * fb < 0:
            return _solve(fn, a, b, tol)
    return None


def _series_fields(corp, it, items, date, acts):
    """CB·BW·EB 회차의 date 기준 입력값 (발행조건 + 조정·잔액 이력 + 병합·분할)."""
    f = dart.parse_cb(it["raw"], it["kind"])
    hist = dart.history(corp["corp_code"], it["bd_tm"], it["kind"], items, date)
    dart.apply_history(f, hist, date)
    dart.adjust_for_actions(f, acts, dart.kdate(it.get("bddd")), date, hist)
    return f


_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


BOOK_GRADES = ("BBB+", "BBB0", "BBB-", "BB+", "BB0", "BB-", "B+", "B0", "B-", "CCC+")
BOOK_TOL = 0.15


def company_spread(corp, items, val_date, ras, cv, fallback="B+"):
    """회사별 할인율 (나이스 등급이 없는 회사용). 평가기준일 직전 정기보고서 주석에 회차별 파생상품부채가 공시된 CB·BW·EB를 골라
    보고기간 말 시장자료로 다시 계산해, 그 장부값(= 회사·외부평가사가 쓴 할인율의 결과)을 맞추는 신용 가정을 찾는다.
      사내 (법인 서버 등급 곡선): BBB+ ~ CCC+ 곡선 중 재계산 파생이 장부에 가장 가까운 등급 (차이 15% 이내만)
      외부 웹 (붙여넣은 Rf 하나): Rd = Rf + s 의 s
    회차별 값이 없고 주석에 할인율(위험이자율)만 있으면 그 중간값 − Rf 를 스프레드로 쓴다. 둘 다 없으면 None."""
    rep = notes.pick_report(items, val_date)
    if not rep:
        return None
    per = rep["period"]
    key = ("cspread", corp["corp_code"], rep["rcept_no"], None if ras is not None else repr((cv.rf, cv.rd)))

    def run():
        allx = _memo(("cb_list", corp["corp_code"]), lambda: dart.cb_list(corp["corp_code"]), ttl=1800)
        groups = {}
        for it in allx:
            groups.setdefault((it["kind"], it["bd_tm"] or it["rcept_no"]), []).append(it)
        latest = sorted((max(xs, key=lambda x: x["rcept_no"]) for xs in groups.values()), key=lambda x: x["rcept_no"], reverse=True)
        acts = dart.corporate_actions(corp["corp_code"], items)
        rf = cv.get(per, fallback)["rf"]
        mk = None
        hint = None
        miss = None
        live = []

        def fit(it, f, v, b, how):
            nonlocal mk
            tag = f"{it['kind']} {it['bd_tm']}회"
            if mk is None:
                mk = _stock(ras, corp["stock_code"], per, corp.get("corp_cls"))
            st = _state(f, per, it["kind"], mk["price"], mk["vol"], rf, None)
            deriv = lambda rd: (lambda r: r["전체가치"] - (r.get("일반사채") or 0))(value_state(dict(st, curves={"rf": rf, "rd": rd})))
            base = {"period": per, "bd_tm": it["bd_tm"], "book": b, "how": "장부 역산"}
            if ras is not None:
                pre = "" if (v.get("사모여부") or "사모") != "공모" else "공모부"
                best = None
                for g in BOOK_GRADES:
                    gap = (deriv(cv.get(per, pre + g)["rd"]) - b) / b
                    if best is None or abs(gap) < abs(best[1]):
                        best = (g, gap)
                if abs(best[1]) <= BOOK_TOL:
                    return dict(base, grade=best[0], gap=best[1],
                                src=f"장부 역산 {best[0]} ({tag} {how} {b / 1e8:,.1f}억 · {per} 재계산 {best[1]:+.1%}"
                                    + (" · 찾는 범위 끝 등급 — 실제는 더 " + ("높을" if best[0] == BOOK_GRADES[0] else "낮을") + " 수 있음" if best[0] in (BOOK_GRADES[0], BOOK_GRADES[-1]) else "") + ")"), None
                return None, f"{tag} {how} {b / 1e8:,.1f}억 — 어느 등급으로도 {BOOK_TOL:.0%} 안에 안 맞음 (가장 가까운 {best[0]} {best[1]:+.0%}, 조건 차이 의심)"
            s = first_root(lambda s: deriv(rd_from_spread(rf, s)) - b, [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.6], b * 1e-3)
            if s is not None:
                return dict(base, spread=s, src=f"장부 역산 Rf+{s * 100:.1f}%p ({tag} {how} {b / 1e8:,.1f}억 · {per})"), None
            return None, f"{tag} {how} {b / 1e8:,.1f}억 — Rf+0~60%p로 맞출 수 없음 (조건 차이 의심)"

        for it in latest[:8]:
            try:
                f = _series_fields(corp, it, items, per, acts)
                v = {k: (x or {}).get("value") for k, x in f.items()}
                if not v.get("발행일") or v["발행일"] > per or not v.get("만기일") or v["만기일"] <= per or not v.get("잔여총액"):
                    continue
                bk = notes.book_values(rep["rcept_no"], it["kind"], it["bd_tm"], v.get("발행총액"))
                live.append((it, f, v, bk))
                T = engine.yf(dt.date.fromisoformat(per), dt.date.fromisoformat(v["만기일"]))
                rates = sorted(float(x) for x in _PCT.findall(bk.get("assume", {}).get("할인율") or ""))
                if rates and hint is None:
                    n = len(rates)
                    mid = rates[n // 2] if n % 2 else (rates[n // 2 - 1] + rates[n // 2]) / 2
                    hint = {"spread": (mid - (_par(engine.RF_TENORS, rf, T) or 0)) / 100, "period": per, "bd_tm": it["bd_tm"],
                            "how": "주석 할인율", "src": f"주석 할인율 {bk['assume']['할인율']} 중간값 − Rf ({it['kind']} {it['bd_tm']}회 · {per})"}
                b = bk.get("derivative")
                if b is None or not str(bk.get("scope") or "").startswith("회차") or b <= 0:
                    continue
                got, why = fit(it, f, v, b, "파생")
                if got:
                    return got
                miss = miss or why
            except Exception:
                continue
        if len(live) == 1 and miss is None:
            it, f, v, bk = live[0]
            tot = bk.get("total_derivative")
            if tot and tot > 0:
                ks = [k for k in (1, 1000, 1e6) if 0.003 <= tot * k / v["잔여총액"] <= 1.5]
                if len(ks) == 1:
                    try:
                        got, why = fit(it, f, v, tot * ks[0], "파생 합계(남은 회차 1개)")
                        if got:
                            return got
                        miss = why
                    except Exception:
                        pass
        if hint and miss:
            hint["src"] += " · " + miss
        return hint or ({"miss": miss} if miss else None)
    return _memo(key, run, ttl=6 * 3600)


def _rates(ras, cv, corp, date, private, credit, cs):
    """곡선 고르기: 나이스 등급(켠 경우, 찾으면) → 회사별 할인율(장부 역산 등급·스프레드 → 주석 할인율) → 일괄 신용 가정."""
    grade, gsrc = _grade(ras, corp, date, private, credit.get("mode"), credit.get("fallback") or "B+")
    nice_hit = gsrc.startswith("나이스 ") and "→" not in gsrc
    if cs and cs.get("grade") and not nice_hit:
        g = cs["grade"] if private else "공모부" + cs["grade"]
        c = cv.get(date, g)
        return c["rf"], c["rd"], g, cs["src"]
    c = cv.get(date, grade)
    if cs and cs.get("spread") is not None and not nice_hit:
        return c["rf"], rd_from_spread(c["rf"], cs["spread"]), f"Rf+{cs['spread'] * 100:.1f}%p", cs["src"]
    if credit.get("company") and not nice_hit:
        gsrc = ((cs or {}).get("miss") or "장부·주석 할인율 없음") + " → " + gsrc
    return c["rf"], c["rd"], grade, gsrc


def _cb_candidates(corp, since):
    """회사의 CB·BW·EB 발행결정(2015~) → (상품, 회차)별 최신 정정본, 기간 안에 공시된 회차만."""
    allx = _memo(("cb_list", corp["corp_code"]), lambda: dart.cb_list(corp["corp_code"]), ttl=1800)
    groups = {}
    for it in allx:
        groups.setdefault((it["kind"], it["bd_tm"] or it["rcept_no"]), []).append(it)
    out = []
    for (kind, _), xs in groups.items():
        if any(dart._iso(x["rcept_no"][:8]) >= since for x in xs):
            out.append(max(xs, key=lambda x: x["rcept_no"]))
    return out


def _rcps_candidates(corp, since, items):
    out = []
    for s in notes.rights_filings([d for d in items if dart._iso(d["rcept_dt"]) >= since]):
        out.append(s)
    return out


def _book_side(row, f, v, kind, corp, items, ras, cv, credit, private, val_date, cs=None):
    """장부 대조: 평가기준일에 맞는 정기보고서 주석의 회차 파생상품부채(공정가치)와 보고기간 말 재계산값."""
    rep = notes.pick_report(items, val_date)
    if not rep or (v.get("발행일") and v["발행일"] > rep["period"]):
        return
    per = rep["period"]
    bk = notes.book_values(rep["rcept_no"], kind, v.get("회차") or row.get("bd_tm"), v.get("발행총액"))
    row["book_period"] = per
    if bk.get("derivative") is None:
        if bk.get("total_derivative"):
            row["notes"].append(f"장부 파생상품부채는 회차 합계만 공시 ({bk['total_derivative'] / 1e8:,.1f}억 · 회차 {len(bk['series_seen'])}개)")
        return
    mk = _stock(ras, corp["stock_code"], per, corp.get("corp_cls"))
    rf, rd, _, _ = _rates(ras, cv, corp, per, private, credit, cs)
    res = value_state(_state(f, per, kind, mk["price"], mk["vol"], rf, rd))
    calc = res["전체가치"] if kind in ("RCPS", "CPS") else res["전체가치"] - (res.get("일반사채") or 0)
    b = bk["derivative"]
    row.update({"book_deriv": b, "calc_deriv": calc, "book_gap": (calc - b) / abs(b) * 100 if b else None,
                "book_scope": bk.get("scope"), "book_model": bk.get("model")})
    if bk.get("scope") == "회차(단일)":
        row["notes"].append("장부 파생 = 공정가치 서열체계 합계 (공시된 회차가 하나)")
    if cs and cs.get("how") == "장부 역산" and str(cs.get("bd_tm")) == str(row.get("bd_tm")) and cs.get("period") == per:
        row["notes"].append("이 회차 장부로 할인율을 역산했으므로 장부 차이 ≈ 0 (검증용 아님)")


def value_corp(corp, val_date, since, kinds=("CB", "BW", "EB"), credit=None, curves=None, at_issue=False, ras=None, book=False):
    """회사 하나의 기간 안 발행 회차를 모두 평가 → 표 행 목록."""
    credit = credit or {}
    cv = curves or Curves(ras)
    items = _memo(("disc", corp["corp_code"]), lambda: dart.disclosures(corp["corp_code"]), ttl=1800)
    rows = []
    cands = []
    if any(k in kinds for k in ("CB", "BW", "EB")):
        for it in _cb_candidates(corp, since):
            if it["kind"] in kinds:
                cands.append(("cb", it))
    if "RCPS" in kinds:
        for s in _rcps_candidates(corp, since, items):
            cands.append(("rcps", s))
    acts = None
    cs = None
    if credit.get("company") and cands:
        try:
            cs = company_spread(corp, items, val_date, ras, cv, credit.get("fallback") or "B+")
        except Exception:
            cs = None
    for typ, it in cands:
        row = {"corp_code": corp["corp_code"], "corp_name": corp["corp_name"], "stock_code": corp.get("stock_code"),
               "corp_cls": corp.get("corp_cls"), "status": "ok", "notes": []}
        try:
            if typ == "cb":
                kind = it["kind"]
                row.update({"kind": kind, "bd_tm": it["bd_tm"], "rcept_no": it["rcept_no"], "key": None})
                if acts is None:
                    acts = dart.corporate_actions(corp["corp_code"], items)
                f = _series_fields(corp, it, items, val_date, acts)
                if kind == "EB":
                    row["notes"].append("EB — 교환대상 주식이 자기주식(발행회사 주식)이라고 보고 평가")
            else:
                rc = it["rcept"]["rcept_no"]
                f = notes.parse_rcps(it, rc)
                raw = (f.pop("_raw", {}) or {}).get("value") or {}
                evs = notes.rcps_events(items, (f.get("발행일") or {}).get("value"), val_date)
                notes.apply_rcps_events(f, evs, val_date)
                notes.apply_pref_call(f, notes.pref_call_text(rc, raw), rc)
                kind = "RCPS" if (f.get("Put초일") or {}).get("value") else "CPS"
                row.update({"kind": kind, "bd_tm": f"{it['rights']['pay']} 납입", "rcept_no": rc, "key": "RIGHTS"})
            v = {k: (x or {}).get("value") for k, x in f.items()}
            row.update({"issue_date": v.get("발행일"), "maturity": v.get("만기일"), "face_issue": v.get("발행총액"),
                        "face": v.get("잔여총액"), "private": v.get("사모여부"), "conv_price": v.get("현조정행사가격"),
                        "first_conv": v.get("최초전환가격"), "refix": v.get("상하향조정"), "floor": v.get("Refix최소"),
                        "coupon": v.get("표면이자율"), "ytm": v.get("만기이자율"), "ytp": v.get("YTP"), "put_start": v.get("Put초일"),
                        "call_ratio": v.get("Call한도") if v.get("Call초일") else None})
            if not v.get("만기일") or v["만기일"] <= val_date:
                row["status"] = "만기 경과"
                rows.append(row)
                continue
            private = (v.get("사모여부") or "사모") != "공모"
            if v.get("발행일") and v["발행일"] > val_date:
                row["status"] = "발행 예정"
                _issue_side(row, f, v, kind, corp, ras, cv, credit, private, mkt_date=val_date, cs=cs)
                rows.append(row)
                continue
            if not v.get("잔여총액"):
                row["status"] = "잔액 0 (전환·상환·취득 완료)"
                rows.append(row)
                continue
            mk = _stock(ras, corp["stock_code"], val_date, corp.get("corp_cls"))
            S = mk["price"]
            div, _ = dart.price_divisor_after(acts or [], mk["price_date"]) if acts else (1.0, [])
            if abs(div - 1) > 1e-9:
                S = S / div
                row["notes"].append(f"평가기준일 이후 병합·분할 → 주가 ÷ {div:g}")
            rf, rd, grade, gsrc = _rates(ras, cv, corp, val_date, private, credit, cs)
            st = _state(f, val_date, kind, S, mk["vol"], rf, rd)
            res = value_state(st)
            face = v["잔여총액"]
            pct = lambda k: (res.get(k) / face * 100) if res.get(k) is not None else None
            if _spy(f, val_date) != 52:
                row["notes"].append(f"만기 {LONG_T}년 초과 — 월 단위 격자로 근사 (회차 리뷰 탭은 주 단위)")
            row.update({"S": S, "sigma": mk["vol"], "parity": S / v["현조정행사가격"] * 100 if v.get("현조정행사가격") else None,
                        "grade": grade, "grade_src": gsrc, "value_pct": pct("전체가치"), "bond_pct": pct("일반사채"),
                        "put_pct": pct("Put"), "conv_pct": pct("전환권"), "call_pct": pct("Call"),
                        "price_date": mk["price_date"], "vol_src": mk["source"]})
            if at_issue and v.get("발행일"):
                _issue_side(row, f, v, kind, corp, ras, cv, credit, private, cs=cs)
            if book:
                try:
                    _book_side(row, f, v, kind, corp, items, ras, cv, credit, private, val_date, cs)
                except Exception as ex:
                    row["notes"].append(f"장부 대조 실패: {str(ex)[:50]}")
        except Exception as ex:
            row["status"] = "평가 불가: " + str(ex)[:120]
        rows.append(row)
    return rows


def _issue_side(row, f, v, kind, corp, ras, cv, credit, private, mkt_date=None, cs=None):
    """발행일 기준: 최초 전환가·발행총액으로 이론가치(발행가 100 기준)와 내재 스프레드.
    mkt_date: 시장자료(주가·변동성·곡선) 날짜 — 발행 예정 회차는 평가기준일 값을 쓴다."""
    iss = v["발행일"]
    md = min(iss, mkt_date) if mkt_date else iss
    f0 = copy.deepcopy(f)
    if v.get("최초전환가격"):
        f0["현조정행사가격"] = {"value": v["최초전환가격"]}
    f0["잔여총액"] = {"value": v.get("발행총액") or v["잔여총액"]}
    try:
        mk = _stock(ras, corp["stock_code"], md, corp.get("corp_cls"))
    except Exception as ex:
        row["notes"].append(f"발행일 주가 없음: {str(ex)[:40]}")
        return
    rf, rd, grade, gsrc = _rates(ras, cv, corp, md, private, credit, cs)
    c = {"rf": rf, "rd": rd}
    target = f0["잔여총액"]["value"]
    res = value_state(_state(f0, iss, kind, mk["price"], mk["vol"], c["rf"], c["rd"]))
    row.update({"issue_S": mk["price"], "issue_sigma": mk["vol"], "issue_grade": grade, "issue_mkt_date": mk["price_date"],
                "issue_value_pct": res["전체가치"] / target * 100,
                "issue_parity": mk["price"] / f0["현조정행사가격"]["value"] * 100 if f0["현조정행사가격"].get("value") else None})
    row.setdefault("grade", grade)
    row.setdefault("grade_src", gsrc)
    row.setdefault("S", mk["price"])
    row.setdefault("sigma", mk["vol"])
    if md != iss:
        row["notes"].append(f"납입 전 — {mk['price_date']} 주가·변동성·곡선으로 발행일({iss}) 기준 계산")
    s, why = implied_spread(f0, iss, kind, mk["price"], mk["vol"], c["rf"], target)
    T = engine.yf(dt.date.fromisoformat(iss), dt.date.fromisoformat(v["만기일"]))
    gs = (_par(engine.RD_TENORS, c["rd"], T) or 0) - (_par(engine.RF_TENORS, c["rf"], T) or 0)
    row.update({"implied_spread": s * 100 if s is not None else None, "grade_spread": gs})
    if why:
        row["notes"].append("내재 스프레드: " + why)
    if ras is None:
        row["notes"].append("발행시점도 입력 곡선으로 계산 (외부 웹은 날짜별 곡선 없음)")
