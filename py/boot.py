"""브라우저 안(Pyodide)에서 대시보드 파이썬을 그대로 돌리기 위한 준비.

- requests 대신 이 모듈의 get/post를 쓴다: DART·야후 주소를 중계 서버(Cloudflare Worker) 주소로 바꿔 동기 XHR로 받는다.
- DART 인증키는 중계 서버가 붙인다. 브라우저·저장소에는 키가 없다.
- 법인 서버(나이스 등급·금리 곡선)는 외부 사이트에서 쓰지 않는다 → 등급·곡선은 직접 입력.
"""
import json
import os
import sys
import types
from urllib.parse import urlencode

import js  # Pyodide: 워커의 전역(self)

sys.path.insert(0, "/app")
os.environ["DART_API_KEY"] = "via-proxy"
PROXY = ""

DART_API = "https://opendart.fss.or.kr/api/"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"


class Resp:
    def __init__(self, status, content, ctype):
        self.status_code, self.content, self.headers = status, content, {"content-type": ctype}

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.content.decode("utf-8"))

    def raise_for_status(self):
        if not 200 <= self.status_code < 400:
            if self.status_code == 0:
                raise RuntimeError("중계 서버에 접속하지 못했습니다. 오른쪽 위 [중계 서버] 주소를 확인하세요.")
            raise RuntimeError(f"중계 서버 응답 {self.status_code}: {self.text[:200]}")


def get(url, params=None, headers=None, timeout=None):
    if not PROXY:
        raise RuntimeError("중계 서버 주소가 없습니다. 오른쪽 위 [중계 서버]에서 주소를 넣어 주세요.")
    params = {k: v for k, v in (params or {}).items() if k != "crtfc_key"}
    if url.startswith(DART_API):
        u = PROXY + "/dart/" + url[len(DART_API):]
    elif url.startswith(YAHOO):
        u = PROXY + "/yahoo/" + url[len(YAHOO):]
    else:
        raise RuntimeError("허용되지 않은 주소입니다: " + url)
    if params:
        u += ("&" if "?" in u else "?") + urlencode(params)
    r = js.syncGet(u)
    return Resp(int(r[0]), r[1].to_bytes(), str(r[2]))


def post(*a, **k):
    raise RuntimeError("법인 서버 조회는 사내 버전에서만 됩니다")


_req = types.ModuleType("requests")
_req.get, _req.post = get, post
sys.modules["requests"] = _req

import api   # noqa: E402
import dart  # noqa: E402

dart._corps = [{"corp_code": a, "corp_name": b, "stock_code": c}
               for a, b, c in json.load(open("/app/data/corps.json", encoding="utf-8"))]


def set_proxy(url):
    global PROXY
    PROXY = (url or "").strip().rstrip("/")


def handle(method, path, query, body):
    """화면의 /api/… 요청 하나 → (상태, 바이트, 콘텐츠타입, 추가헤더 JSON)"""
    try:
        code, payload, ctype, extra = api.route(method, path, query, body)
    except Exception as ex:
        code, payload, ctype, extra = 400, {"error": str(ex)}, api.JSON, None
    if isinstance(payload, (bytes, bytearray)):
        data = bytes(payload)
    elif isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    return code, data, ctype, json.dumps(extra or {})
