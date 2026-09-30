"""The desktop app in a browser, where no native window can open.

Windows builds show the page through WebView2. Linux and macOS builds
would need GTK or Cocoa bindings that PyInstaller cannot carry portably,
and a machine reached over SSH has no display at all. There acg serves
the same page on 127.0.0.1 and the browser shows it; over SSH, a
forwarded port does the same.

The bridge can apply and push configuration, so every call must prove it
comes from the page acg opened:

- the server listens on 127.0.0.1 only;
- the launch URL carries a one-time code that the page trades for a
  session token, so the copy visible in a process list is spent once the
  browser has used it;
- every call carries the token in a header, and a JSON content type,
  which a page on another origin cannot send without a CORS preflight
  this server never answers;
- the Host header must name this machine, which stops DNS rebinding,
  and an Origin header, when sent, must match it.
"""

import hmac
import http.server
import inspect
import json
import mimetypes
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

MAX_BODY = 1 << 20
# 還沒被打開前給久一點:SSH 的人要複製網址、開轉發
IDLE_BEFORE_CLAIM = 600.0
# 頁面每 20 秒回報一次;關掉分頁後不久就結束,不留一個沒人用的伺服器
IDLE_AFTER_CLAIM = 90.0
_LOCAL_NAMES = {"127.0.0.1", "localhost", "[::1]", "::1"}
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class Session:
    """The bridge object, the one-time code, and the token it turned into."""

    def __init__(self, api: object) -> None:
        self.api = api
        self.code: str | None = secrets.token_urlsafe(24)
        self.token: str | None = None
        self.last_seen = time.monotonic()
        self._lock = threading.Lock()
        # pywebview 開放的就是這些:類別上不以底線開頭的方法
        self.methods = {
            name for name, member in inspect.getmembers(type(api))
            if not name.startswith("_") and callable(member)
        }

    def claim(self, code: object) -> "str | None":
        with self._lock:
            if (
                self.code is None
                or not isinstance(code, str)
                or not hmac.compare_digest(code.encode(), self.code.encode())
            ):
                return None
            self.code = None
            self.token = secrets.token_urlsafe(32)
            self.touch()
            return self.token

    def authorized(self, token: "str | None") -> bool:
        return (
            self.token is not None
            and token is not None
            and hmac.compare_digest(token.encode(), self.token.encode())
        )

    def touch(self) -> None:
        self.last_seen = time.monotonic()

    def idle_limit(self) -> float:
        return IDLE_AFTER_CLAIM if self.token else IDLE_BEFORE_CLAIM


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, session: Session, assets: Path, port: int = 0) -> None:
        self.session = session
        self.assets = assets.resolve()
        super().__init__(("127.0.0.1", port), _Handler)

    @property
    def port(self) -> int:
        return self.server_address[1]


def _hostname(value: str) -> str:
    return (urlsplit(f"//{value}").hostname or "").lower()


class _Handler(http.server.BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        return

    def _local_request(self) -> bool:
        host = self.headers.get("Host", "")
        if _hostname(host) not in _LOCAL_NAMES:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin == f"http://{host}"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in _SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if not self._local_request():
            self._json(403, {"error": "forbidden"})
            return
        path = urlsplit(self.path).path
        root = self.server.assets
        target = (root / (path.lstrip("/") or "index.html")).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            self._json(404, {"error": "not found"})
            return
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            kind = "text/javascript"
        self._send(200, target.read_bytes(), f"{kind}; charset=utf-8" if kind.startswith("text/") else kind)

    do_HEAD = do_GET

    def do_POST(self) -> None:
        if not self._local_request():
            self._json(403, {"error": "forbidden"})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "JSON only"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self._json(413, {"error": "body too large"})
            self.close_connection = True
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"null")
        except ValueError:
            self._json(400, {"error": "invalid JSON"})
            return
        path = urlsplit(self.path).path
        session = self.server.session
        if path == "/api/__claim":
            token = session.claim(payload.get("code") if isinstance(payload, dict) else None)
            if token is None:
                self._json(403, {"error": "this link was already used; run acg desktop again"})
            else:
                self._json(200, {"token": token})
            return
        if not session.authorized(self.headers.get("X-Acg-Token")):
            self._json(403, {"error": "forbidden"})
            return
        session.touch()
        method = path.removeprefix("/api/")
        if method == "__ping":
            self._json(200, {"result": True})
            return
        if not path.startswith("/api/") or method not in session.methods:
            self._json(404, {"error": f"no such method: {method}"})
            return
        if not isinstance(payload, list):
            self._json(400, {"error": "arguments must be a list"})
            return
        try:
            result = getattr(session.api, method)(*payload)
        except TypeError as exc:
            self._json(400, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 - the page shows it, the server stays up
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return
        self._json(200, {"result": result})


def _stop_when_idle(server: Server, poll: float = 5.0) -> None:
    session = server.session
    while True:
        time.sleep(poll)
        if time.monotonic() - session.last_seen > session.idle_limit():
            server.shutdown()
            return


def _announce(line: str) -> None:
    # 網址要馬上看得到:輸出被導到檔案或管線時,print 會一直緩衝到結束
    print(line, flush=True)


def serve(api: object, assets: Path, port: int = 0, open_browser: bool = True,
          announce=_announce, poll: float = 5.0) -> int:
    """Serve until the page goes quiet. The caller has checked assets exist."""
    session = Session(api)
    try:
        server = Server(session, assets, port)
    except OSError as exc:
        announce(f"✗ 無法在 127.0.0.1:{port} 開啟:{exc}")
        return 1
    url = f"http://127.0.0.1:{server.port}/#c={session.code}"
    announce(f"acg desktop:{url}")
    opened = False
    if open_browser:
        import webbrowser

        opened = webbrowser.open(url)
    if not opened:
        announce(
            f"在遠端機器上的話,先在自己的電腦執行 ssh -L {server.port}:127.0.0.1:{server.port} <這台機器>,"
            "再用瀏覽器打開上面的網址"
        )
    announce("關掉分頁後約一分半會自動結束;Ctrl+C 可以立即結束")
    threading.Thread(target=_stop_when_idle, args=(server, poll), daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        discard = getattr(api, "_discard_previews", None)
        if callable(discard):
            discard()
    return 0
