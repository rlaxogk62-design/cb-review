/**
 * CB 리뷰 대시보드 중계 서버 — Vercel 서버리스 함수, 서울 리전(icn1).
 * DART는 해외 IP 접속을 막아서(Cloudflare 무료는 홍콩에서 실행됨) 한국 리전에서 돌린다.
 *
 *  /dart/<API>?<인자>   → https://opendart.fss.or.kr/api/<API>  (인증키는 환경변수 DART_API_KEY — 사이트·저장소에는 없음)
 *  /yahoo/<종목>?<인자> → 야후 파이낸스 차트 API
 *  /ping                → 연결·키 설정 확인
 * 받는 것은 공개 정보 조회 요청뿐이다. 평가 입력값·결과는 오지 않는다.
 * 오류 문구에 요청 주소를 넣지 않는다 (주소에 인증키가 있음).
 */
const DART_APIS = new Set([
  'list.json', 'cvbdIsDecsn.json', 'bdwtIsDecsn.json', 'exbdIsDecsn.json', 'document.xml',
  'company.json', 'fnlttSinglAcnt.json', 'accnutAdtorNmNdAdtOpinion.json',
]);
const LOCAL = /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/;

function allow(origin) {
  const list = (process.env.ALLOWED_ORIGIN || 'https://rlaxogk62-design.github.io')
    .split(',').map((s) => s.trim().replace(/\/+$/, '')).filter(Boolean);
  if (origin && (list.includes(origin) || LOCAL.test(origin))) return origin;
  return null;
}

export default async function handler(req, res) {
  const origin = req.headers.origin || '';
  const ok = allow(origin);
  res.setHeader('Access-Control-Allow-Origin', ok || 'null');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  res.setHeader('Vary', 'Origin');
  const text = (status, body) => { res.setHeader('Content-Type', 'text/plain; charset=utf-8'); res.status(status).send(body); };
  if (!ok) return text(403, '허용되지 않은 사이트입니다 (ALLOWED_ORIGIN 확인).');
  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method !== 'GET') return text(405, 'GET 요청만 받습니다.');

  const url = new URL(req.url, 'http://local');
  const [kind, name] = (url.searchParams.get('__p') || '').split('/').filter(Boolean);
  url.searchParams.delete('__p');
  const key = process.env.DART_API_KEY;
  if (kind === 'ping') {
    res.setHeader('Content-Type', 'application/json');
    return res.status(200).send(JSON.stringify({ ok: true, dart: !!key, region: process.env.VERCEL_REGION || null }));
  }
  let targets;
  let headers = {};
  if (kind === 'dart' && DART_APIS.has(name)) {
    if (!key) return text(500, '중계 서버에 DART_API_KEY가 없습니다.');
    url.searchParams.set('crtfc_key', key);
    targets = [`https://opendart.fss.or.kr/api/${name}?${url.searchParams}`];
  } else if (kind === 'yahoo' && /^[0-9A-Z]{6}\.(KS|KQ)$/.test(name || '')) {
    targets = ['query1', 'query2'].map((h) => `https://${h}.finance.yahoo.com/v8/finance/chart/${name}?${url.searchParams}`);
    headers = { 'User-Agent': 'Mozilla/5.0' };
  } else {
    return text(404, '지원하지 않는 경로입니다.');
  }

  const errors = [];
  for (const t of targets) {
    try {
      const r = await fetch(t, { headers, redirect: 'manual' });
      if (r.status >= 300 && r.status < 400) {
        errors.push((r.headers.get('location') || '').includes('error') ? '원본 서버가 이 중계 서버 위치에서의 접속을 막음' : `리다이렉트 ${r.status}`);
        continue;
      }
      if (!r.ok && t !== targets[targets.length - 1]) { errors.push(`HTTP ${r.status}`); continue; }
      const buf = Buffer.from(await r.arrayBuffer());
      res.setHeader('Content-Type', r.headers.get('content-type') || 'application/octet-stream');
      res.setHeader('Cache-Control', name === 'document.xml' ? 'public, max-age=86400' : 'no-store');
      return res.status(r.status).send(buf);
    } catch (e) {
      errors.push(e.name || '접속 오류');
    }
  }
  return text(502, '원본 서버 접속 실패: ' + errors.join(' / '));
}
