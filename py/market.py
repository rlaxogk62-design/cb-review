"""주가·변동성 · 신용등급 → 곡선 등급코드.

주가는 사내 버전이면 법인 서버(ras.py, 엑셀 Temp_v530과 같은 출처)를 먼저 쓰고, 안 되면 야후 파이낸스 차트 API로 받는다.
나이스 등급·금리 곡선 조회도 ras.py에 있다 (외부 사이트에는 없음 → 직접 입력).

변동성은 Temp S_vol 시트와 같다:
  일간 로그수익률 = LN(오늘/직전 거래일), 일 변동성 = STDEV.S(최근 180개),
  연 변동성 = 일 변동성 × √(평가기준일 이전 1년 동안의 관측일 수)
"""
import calendar
import datetime as dt
import math

import requests


def _d(s):
    return dt.date.fromisoformat(s) if isinstance(s, str) else s


def edate(d, months):
    d = _d(d)
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def prices_yahoo(code, market, dday, months=14):
    suffix = {"Y": "KS", "K": "KQ"}.get(market or "", "KS")
    end = _d(dday) + dt.timedelta(days=1)
    start = edate(end, -months)
    p1 = int(dt.datetime(start.year, start.month, start.day, tzinfo=dt.timezone.utc).timestamp())
    p2 = int(dt.datetime(end.year, end.month, end.day, tzinfo=dt.timezone.utc).timestamp())
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.{suffix}",
                     params={"period1": p1, "period2": p2, "interval": "1d", "events": "history"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=40)
    r.raise_for_status()
    res = (r.json().get("chart") or {}).get("result") or []
    if not res:
        raise RuntimeError("야후 파이낸스에 시세가 없습니다.")
    ts = res[0].get("timestamp") or []
    close = ((res[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    kst = dt.timezone(dt.timedelta(hours=9))
    rows = [(dt.datetime.fromtimestamp(t, kst).date().isoformat(), float(c)) for t, c in zip(ts, close) if c is not None]
    rows = [x for x in rows if x[0] <= _d(dday).isoformat()]
    rows.sort(reverse=True)
    return rows, f"야후 파이낸스 직접 ({code}.{suffix})", (res[0].get("meta") or {}).get("longName")


def vol_temp(rows, dday, window=180):
    """Temp S_vol: rows = [(날짜, 종가)] 최신순."""
    if len(rows) < 3:
        raise ValueError("주가 데이터가 부족합니다.")
    rets = [math.log(a[1] / b[1]) for a, b in zip(rows, rows[1:]) if a[1] > 0 and b[1] > 0]
    use = rets[:window]
    mu = sum(use) / len(use)
    sd = math.sqrt(sum((x - mu) ** 2 for x in use) / (len(use) - 1))
    lo = edate(dday, -12).isoformat()
    n_year = sum(1 for d, _ in rows if lo < d <= _d(dday).isoformat())
    return {"daily": sd, "vol": sd * math.sqrt(n_year), "n_returns": len(use), "n_year": n_year}


def _quartile_inc(x, q):
    """엑셀 QUARTILE.INC"""
    s = sorted(x)
    h = (len(s) - 1) * q
    lo = math.floor(h)
    return s[lo] + (h - lo) * (s[min(lo + 1, len(s) - 1)] - s[lo])


def vol_iqr(rows, dday, annual=250):
    """평가사(KAP) backdata '상장 아웃라이어제거' 방식: 평가기준일 이전 1년 일간 로그수익률을
    Q1−1.5×IQR ~ Q3+1.5×IQR 밖이면 경계값으로 바꾼 뒤 STDEV.S × √250."""
    lo = edate(dday, -12).isoformat()
    d0 = _d(dday).isoformat()
    rets = [math.log(a[1] / b[1]) for a, b in zip(rows, rows[1:]) if lo < a[0] <= d0 and a[1] > 0 and b[1] > 0]
    if len(rets) < 20:
        raise ValueError("1년 수익률이 부족합니다.")
    q1, q3 = _quartile_inc(rets, 0.25), _quartile_inc(rets, 0.75)
    a, b = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    c = [min(max(x, a), b) for x in rets]
    mu = sum(c) / len(c)
    sd = math.sqrt(sum((x - mu) ** 2 for x in c) / (len(c) - 1))
    return {"vol": sd * math.sqrt(annual), "daily": sd, "n": len(c), "clipped": sum(1 for x in rets if x < a or x > b),
            "lo": a, "hi": b, "annual": annual}


def stock(code, dday, market=None, period_type="1년", maturity=None, window=180, ras=None):
    """ras: 사내 전용 법인 서버 모듈(ras.py). 주면 먼저 쓰고, 안 되면 야후."""
    errors = []
    sources = []
    if ras is not None:
        sources.append(("법인 서버", lambda: ras.prices_ras(code, dday, period_type, maturity)))
    if market in ("Y", "K"):
        sources.append(("야후", lambda: prices_yahoo(code, market, dday)))
    else:
        sources.append(("야후(KS)", lambda: prices_yahoo(code, "Y", dday)))
        sources.append(("야후(KQ)", lambda: prices_yahoo(code, "K", dday)))
    for label, fetch in sources:
        try:
            rows, src, name = fetch()
            if not rows:
                raise RuntimeError("시세가 비어 있습니다.")
            v = vol_temp(rows, dday, window)
            try:
                vi = vol_iqr(rows, dday)
            except Exception:
                vi = None
            return {"price": rows[0][1], "price_date": rows[0][0], "vol": v["vol"], "daily_vol": v["daily"],
                    "n_returns": v["n_returns"], "n_year": v["n_year"], "vol_iqr": vi, "source": src, "name": name,
                    "series": rows[:260], "errors": errors}
        except Exception as ex:
            errors.append(f"{label}: {ex}")
    raise RuntimeError(" / ".join(errors))


GRADE_ORDER = ["AAA", "AA+", "AA0", "AA-", "A+", "A0", "A-", "BBB+", "BBB0", "BBB-", "BB+", "BB0", "BB-",
               "B+", "B0", "B-", "CCC+", "CCC0", "CCC-", "CC", "C", "D"]


def grade_asof(rows, val_date):
    """평가기준일 이전 가장 최근 등급."""
    vd = _d(val_date).isoformat()
    past = [r for r in rows if (r.get("평가일자") or "") <= vd and r.get("신용등급")]
    return past[0] if past else None


def curve_code(grade, private=True):
    """나이스 기업신용등급 → 곡선 등급코드 (Temp DU·EL열).
    사모(사모무보증) = 등급 그대로('B0','BBB-'), 공모(공모무보증) = '공모부' + 등급."""
    if not grade:
        return None
    g = grade.strip().upper()
    return g if private else "공모부" + g


def city_from_address(adres):
    """DART 회사 주소 → 소재시군 ('경기도 화성시 …' → '화성', '서울특별시 …' → '서울')."""
    if not adres:
        return None
    parts = adres.replace("(", " ").split()
    big = {"서울": "서울", "부산": "부산", "대구": "대구", "인천": "인천", "광주": "광주", "대전": "대전", "울산": "울산", "세종": "세종"}
    for k, v in big.items():
        if parts and parts[0].startswith(k):
            return v
    if len(parts) >= 2:
        t = parts[1]
        for suf in ("특별자치시", "시", "군"):
            if t.endswith(suf) and len(t) > len(suf):
                return t[: -len(suf)]
        return t
    return None
