/* 브라우저 안 파이썬(Pyodide) 작업자: 사내 대시보드와 같은 파이썬 코드(dart·engine·export…)를 그대로 돌린다. */
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/';
importScripts(PYODIDE + 'pyodide.js');
const PY_FILES = ['dart.py', 'engine.py', 'export.py', 'market.py', 'api.py', 'boot.py'];

// 파이썬 쪽 requests 대용이 부르는 동기 GET (작업자 안에서는 동기 XHR을 쓸 수 있다)
self.syncGet = (url) => {
  const x = new XMLHttpRequest();
  x.open('GET', url, false);
  x.responseType = 'arraybuffer';
  try { x.send(); } catch (e) { return [0, new Uint8Array(0), '']; }
  return [x.status, new Uint8Array(x.response || new ArrayBuffer(0)), x.getResponseHeader('content-type') || ''];
};

const say = (text) => postMessage({ type: 'status', text });
let py, boot;

async function start(proxy) {
  say('파이썬 엔진 내려받는 중…');
  py = await loadPyodide({ indexURL: PYODIDE });
  say('엑셀 모듈(openpyxl) 설치 중…');
  await py.loadPackage('micropip');
  await py.runPythonAsync("import micropip\nawait micropip.install('openpyxl')");
  say('대시보드 코드 불러오는 중…');
  py.FS.mkdirTree('/app/data/cache');
  const v = self.location.search;            // 캐시 무효화용 버전 꼬리표
  await Promise.all(PY_FILES.map(async (f) => {
    const r = await fetch('../py/' + f + v);
    if (!r.ok) throw new Error(f + ' 불러오기 실패');
    py.FS.writeFile('/app/' + f, await r.text());
  }));
  const corps = await fetch('../data/corps.json' + v);
  py.FS.writeFile('/app/data/corps.json', new Uint8Array(await corps.arrayBuffer()));
  py.runPython("import sys; sys.path.insert(0, '/app'); import boot");
  boot = py.pyimport('boot');
  boot.set_proxy(proxy || '');
  say('');
  postMessage({ type: 'ready' });
}

let ready = null;
onmessage = async (e) => {
  const m = e.data;
  try {
    if (m.type === 'boot') {
      ready = start(m.proxy);
      await ready;
      return;
    }
    if (!ready) throw new Error('엔진이 아직 시작되지 않았습니다.');
    await ready;
    if (m.type === 'proxy') { boot.set_proxy(m.proxy || ''); postMessage({ id: m.id, ok: true }); return; }
    if (m.type === 'route') {
      const out = boot.handle(m.method, m.path, m.query || '', m.body || null);
      const [status, data, ctype, headers] = out.toJs();
      out.destroy();
      const bytes = data.toJs ? data.toJs() : data;
      postMessage({ id: m.id, status, data: bytes, ctype, headers });
    }
  } catch (err) {
    if (m.type === 'boot') postMessage({ type: 'fail', text: String(err.message || err) });
    else postMessage({ id: m.id, error: String(err.message || err) });
  }
};
