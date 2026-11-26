/* 외부 사이트용 연결부: 화면(app.js)이 부르는 /api/… 요청을 브라우저 안 파이썬 작업자로 보낸다.
 * app.js는 사내 버전과 같은 파일을 쓴다. */
window.CBR_WEB = true;
(() => {
  const VER = (window.CBR_CONFIG || {}).version || '';
  const proxy = () => localStorage.getItem('cbrProxy') || (window.CBR_CONFIG || {}).proxy || '';
  const worker = new Worker('js/py-worker.js?v=' + VER);
  const pend = new Map();
  let seq = 0;
  let readyResolve, readyReject;
  const ready = new Promise((res, rej) => { readyResolve = res; readyReject = rej; });

  const status = (text, err) => {
    const el = document.getElementById('engineStatus');
    if (!el) return;
    el.textContent = text || '엔진 준비됨';
    el.className = 'badge' + (err ? ' err' : text ? ' busy' : '');
  };

  worker.onmessage = (e) => {
    const m = e.data;
    if (m.type === 'status') return status(m.text);
    if (m.type === 'ready') { status(''); return readyResolve(); }
    if (m.type === 'fail') { status('엔진 오류', true); return readyReject(new Error(m.text)); }
    const p = pend.get(m.id);
    if (!p) return;
    pend.delete(m.id);
    if (m.error) p.reject(new Error(m.error)); else p.resolve(m);
  };
  const call = (msg) => new Promise((resolve, reject) => {
    const id = ++seq;
    pend.set(id, { resolve, reject });
    worker.postMessage({ ...msg, id });
  });
  worker.postMessage({ type: 'boot', proxy: proxy() });

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input.url;
    if (!url.startsWith('/api/')) return realFetch(input, init);
    await ready;
    const u = new URL(url, 'http://x');
    const m = await call({ type: 'route', method: init.method || 'GET', path: u.pathname, query: u.search.slice(1), body: init.body || null });
    const h = new Headers(JSON.parse(m.headers || '{}'));
    h.set('content-type', m.ctype);
    return new Response(m.data, { status: m.status, headers: h });
  };

  /* 중계 서버 주소 설정 */
  async function ping(url) {
    const r = await realFetch(url.replace(/\/+$/, '') + '/ping');
    const j = await r.json();
    if (!j.dart) throw new Error('중계 서버에 DART_API_KEY가 설정되지 않았습니다.');
  }
  function openProxy() {
    const box = document.getElementById('proxyBox');
    box.classList.remove('hidden');
    document.getElementById('proxyUrl').value = proxy();
  }
  window.addEventListener('DOMContentLoaded', () => {
    status('파이썬 엔진 준비 중…');
    document.getElementById('btnProxy').onclick = openProxy;
    document.getElementById('btnProxyClose').onclick = () => document.getElementById('proxyBox').classList.add('hidden');
    document.getElementById('btnProxySave').onclick = async () => {
      const v = document.getElementById('proxyUrl').value.trim().replace(/\/+$/, '');
      const msg = document.getElementById('proxyMsg');
      msg.textContent = '확인 중…';
      try {
        await ping(v);
        localStorage.setItem('cbrProxy', v);
        await call({ type: 'proxy', proxy: v });
        msg.textContent = '연결됨. 저장했습니다.';
        setTimeout(() => document.getElementById('proxyBox').classList.add('hidden'), 800);
      } catch (e) { msg.textContent = '연결 실패: ' + e.message; }
    };
    if (!proxy()) openProxy();
  });
})();
