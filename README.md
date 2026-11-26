# CB 리뷰 대시보드 (웹)

상장사 CB·BW·EB 발행결정 공시를 DART에서 불러와 평가 입력값을 채우고, 근거 문구를 원문에 형광펜으로 표시하고,
유의 포인트를 띄우고, 이항모형(TF)으로 평가한 뒤 리뷰 엑셀·Temp DW 붙여넣기 엑셀을 만드는 사이트입니다.

**사이트:** https://rlaxogk62-design.github.io/cb-review/

## 무엇이 자동이고 무엇이 직접 입력인가

| 항목 | 출처 |
|---|---|
| 발행조건·전환가 조정 이력·잔액·Put/Call 일정 | DART 공시 (자동, 근거 형광펜) |
| 유의 포인트 (리픽싱·만기 전 취득·감사의견·영업손실 등) | DART 공시 규칙 검사 (자동) |
| 주가·변동성 (Temp S_vol 방식 / 1년 IQR 이상치 조정 방식) | 야후 파이낸스 (자동) |
| **신용등급 → 곡선 등급코드** | **직접 선택** |
| **Rf·Rd 할인율 곡선** | **직접 붙여넣기** (Temp DW의 DV~EK · EM~EZ) |

## 데이터는 어디로 가나

- 사이트는 정적 페이지입니다. 계산 코드(파이썬)는 [Pyodide](https://pyodide.org)로 **브라우저 안에서** 돌아갑니다.
- 입력값·평가 결과·엑셀은 브라우저 밖으로 나가지 않습니다. 어디에도 저장되지 않습니다.
- 밖으로 나가는 요청은 **DART 공시 조회와 주가 조회**뿐이고, 아래 중계 서버(본인 Cloudflare 계정)를 거칩니다.
  중계 서버가 아는 것은 어떤 회사의 공시·주가를 조회했는지뿐입니다.
- 처음 열 때 Pyodide(jsDelivr)와 openpyxl(PyPI)을 내려받습니다 (라이브러리만, 데이터 없음).

## 중계 서버 설정

브라우저는 보안 규칙(CORS) 때문에 DART·야후에 직접 접속할 수 없습니다. Cloudflare Workers(무료, 하루 10만 건)에 중계 서버를 하나 만듭니다.

1. <https://dash.cloudflare.com> 가입(무료) → 로그인
2. **Workers & Pages → Create → Worker** → 이름 `cb-review-proxy` → **Deploy**
3. **Edit code** → 이 저장소의 [`worker/worker.js`](worker/worker.js) 내용을 통째로 붙여넣기 → **Deploy**
4. Worker의 **Settings → Variables and Secrets** 에서 추가 → **Deploy**
   - `DART_API_KEY` — 종류 **Secret**, 값: DART OpenAPI 인증키 (<https://opendart.fss.or.kr> 에서 발급)
   - `ALLOWED_ORIGIN` — 종류 Text, 값: `https://rlaxogk62-design.github.io`
5. Worker 주소(`https://cb-review-proxy.<계정>.workers.dev`)를 복사 → 사이트 오른쪽 위 **[중계 서버 설정]** 에 붙여넣고 **연결 확인·저장**

주소는 브라우저에 기억됩니다. 다른 사람도 바로 쓰게 하려면 사내 버전의 `web/site.json`의 `proxy`에 주소를 넣고 다시 빌드·업로드하세요.

## 업데이트

이 저장소는 사내 대시보드에서 `build_web.py`로 만들어집니다 (법인 서버 연동 코드·검증 사례·키는 빌드에서 빠짐).

```
py -3.13 -X utf8 build_web.py
cd ..\cb-review-web
git add -A
git commit -m "업데이트"
git push
```

## 계산 규칙 요약

- Temp_v530 시트 수식과 같은 TF 이항모형: Δt = 1/52, 노드 = MIN(Call, MAX(전환가치, Put+쿠폰, 보유가치)), 선순위 Call은 Call 말일까지 전환 불가,
  Call = 한도 × (Call 있음 − Call 없음), 리픽싱은 X 시트 경로확률 방식.
- 금리 곡선은 3개월 이표 par YTM으로 보고 zero 금리로 부트스트랩.
- 결과는 **참고 추정치**입니다. 확정 값은 Temp 계산 결과를 쓰세요.
