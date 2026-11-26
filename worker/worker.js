/**
 * CB 리뷰 대시보드 중계 서버 (Cloudflare Workers 무료 요금제로 충분)
 *
 * 브라우저는 DART·야후에 직접 접속할 수 없어서(CORS) 이 중계 서버가 대신 받아 온다.
 *  - /dart/<API>?<인자>   → https://opendart.fss.or.kr/api/<API>   (DART 인증키는 여기서 붙인다 — 사이트·저장소에는 키가 없다)
 *  - /yahoo/<종목>?<인자> → https://query1.finance.yahoo.com/v8/finance/chart/<종목>  (실패하면 query2)
 *  - /ping                → 연결·키 설정 확인
 * 받는 것은 공개 정보 조회 요청뿐이다. 평가 입력값·결과는 이 서버로 오지 않는다.
 *
 * 설정 (Cloudflare 대시보드 → 이 Worker → Settings → Variables and Secrets):
 *  - DART_API_KEY   : DART OpenAPI 인증키 (종류: Secret)
 *  - ALLOWED_ORIGIN : 사이트 주소, 예) https://rlaxogk62-design.github.io   (쉼표로 여러 개 가능)
 */
const DART_APIS = new Set([
  'list.json', 'cvbdIsDecsn.json', 'bdwtIsDecsn.json', 'exbdIsDecsn.json', 'document.xml',
  'company.json', 'fnlttSinglAcnt.json', 'accnutAdtorNmNdAdtOpinion.json',
]);
const LOCAL = /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/;

function allow(origin, env) {
  const list = (env.ALLOWED_ORIGIN || '').split(',').map((s) => s.trim().replace(/\/+$/, '')).filter(Boolean);
  if (!list.length) return origin || '*';                       // 아직 설정 전: 모두 허용 (설정 권장)
  if (origin && (list.includes(origin) || LOCAL.test(origin))) return origin;
  return null;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const ok = allow(request.headers.get('Origin') || '', env);
    const cors = {
      'Access-Control-Allow-Origin': ok || 'null',
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Vary': 'Origin',
    };
    const text = (body, status) => new Response(body, { status, headers: { ...cors, 'Content-Type': 'text/plain; charset=utf-8' } });
    if (!ok) return text('허용되지 않은 사이트입니다 (ALLOWED_ORIGIN 확인).', 403);
    if (request.method === 'OPTIONS') return new Response(null, { headers: cors });
    if (request.method !== 'GET') return text('GET 요청만 받습니다.', 405);

    const [kind, name] = url.pathname.split('/').filter(Boolean);
    if (kind === 'ping') {
      return new Response(JSON.stringify({ ok: true, dart: !!env.DART_API_KEY }), { headers: { ...cors, 'Content-Type': 'application/json' } });
    }
    let targets;
    let init = {};
    if (kind === 'dart' && DART_APIS.has(name)) {
      if (!env.DART_API_KEY) return text('중계 서버에 DART_API_KEY가 없습니다.', 500);
      const q = new URLSearchParams(url.search);
      q.set('crtfc_key', env.DART_API_KEY);
      targets = [`https://opendart.fss.or.kr/api/${name}?${q}`];
    } else if (kind === 'yahoo' && /^[0-9A-Z]{6}\.(KS|KQ)$/.test(name || '')) {
      targets = ['query1', 'query2'].map((h) => `https://${h}.finance.yahoo.com/v8/finance/chart/${name}${url.search}`);
      init = { headers: { 'User-Agent': 'Mozilla/5.0' } };
    } else {
      return text('지원하지 않는 경로입니다.', 404);
    }

    let r;
    for (const t of targets) {
      r = await fetch(t, init);
      if (r.ok) break;
    }
    const out = new Response(r.body, r);
    for (const [k, v] of Object.entries(cors)) out.headers.set(k, v);
    out.headers.delete('Set-Cookie');
    out.headers.set('Cache-Control', name === 'document.xml' ? 'public, max-age=86400' : 'no-store');
    return out;
  },
};
