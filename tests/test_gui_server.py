"""Browser mode: only the page acg opened may drive the bridge."""

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from ai_config import gui_server


class FakeApi:
    def __init__(self) -> None:
        self.calls: list = []
        self.discarded = False

    def get_info(self) -> dict:
        return {"version": "test"}

    def run(self, command: str) -> dict:
        self.calls.append(command)
        return {"code": 0, "output": command}

    def boom(self) -> None:
        raise RuntimeError("broken")

    def _discard_previews(self) -> None:
        self.discarded = True


@pytest.fixture
def served(tmp_path: Path):
    assets = tmp_path / "assets"
    (assets / "assets").mkdir(parents=True)
    (assets / "index.html").write_text("<p>acg</p>", encoding="utf-8")
    (assets / "assets" / "app.js").write_text("1", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    api = FakeApi()
    session = gui_server.Session(api)
    server = gui_server.Server(session, assets)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield server, session, api
    server.shutdown()
    server.server_close()


def request(server, method: str, path: str, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    sent = {"Host": f"127.0.0.1:{server.port}"}
    if body is not None:
        sent["Content-Type"] = "application/json"
        body = json.dumps(body)
    sent.update(headers or {})
    connection.request(method, path, body=body, headers=sent)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, (json.loads(data) if data.startswith((b"{", b"[")) else data), response


def claim(server, session) -> str:
    status, body, _ = request(server, "POST", "/api/__claim", {"code": session.code})
    assert status == 200
    return body["token"]


def test_the_launch_code_works_once(served) -> None:
    """The URL can sit in a process list; once the browser used it, it is spent."""
    server, session, _api = served
    code = session.code

    assert request(server, "POST", "/api/__claim", {"code": "guess"})[0] == 403
    token = claim(server, session)

    assert token
    assert request(server, "POST", "/api/__claim", {"code": code})[0] == 403


def test_calls_need_the_session_token(served) -> None:
    server, session, api = served
    token = claim(server, session)

    assert request(server, "POST", "/api/run", ["push"])[0] == 403
    assert request(server, "POST", "/api/run", ["push"], {"X-Acg-Token": "guess"})[0] == 403
    status, body, _ = request(server, "POST", "/api/run", ["status"], {"X-Acg-Token": token})

    assert status == 200 and body["result"]["output"] == "status"
    assert api.calls == ["status"]


def test_only_what_pywebview_would_expose_can_be_called(served) -> None:
    server, session, _api = served
    token = claim(server, session)
    auth = {"X-Acg-Token": token}

    assert request(server, "POST", "/api/_discard_previews", [], auth)[0] == 404
    assert request(server, "POST", "/api/__init__", [], auth)[0] == 404
    assert request(server, "POST", "/api/boom", [], auth)[0] == 500
    assert request(server, "POST", "/api/run", {"command": "x"}, auth)[0] == 400


def test_a_page_on_another_origin_cannot_reach_it(served) -> None:
    """DNS rebinding names another host; a form post cannot send JSON."""
    server, session, _api = served

    assert request(server, "GET", "/", headers={"Host": f"evil.example:{server.port}"})[0] == 403
    assert request(server, "POST", "/api/__claim", {"code": session.code},
                   {"Origin": "http://evil.example"})[0] == 403
    status, _body, _ = request(server, "POST", "/api/__claim", None,
                               {"Content-Type": "text/plain", "Content-Length": "0"})
    assert status == 415
    # 上面幾次都沒有把代碼用掉
    assert claim(server, session)


def test_ssh_forwarding_to_another_local_port_still_works(served) -> None:
    """ssh -L 8765:127.0.0.1:<port> makes the browser say localhost:8765."""
    server, session, _api = served

    status, _body, _ = request(server, "POST", "/api/__claim", {"code": session.code},
                               {"Host": "localhost:8765", "Origin": "http://localhost:8765"})

    assert status == 200


def test_static_files_stay_inside_the_assets(served) -> None:
    server, _session, _api = served

    status, body, response = request(server, "GET", "/")
    assert status == 200 and body == b"<p>acg</p>"
    assert response.getheader("X-Frame-Options") == "DENY"
    assert request(server, "GET", "/assets/app.js")[2].getheader("Content-Type").startswith("text/javascript")
    assert request(server, "GET", "/../secret.txt")[0] == 404


def test_an_oversized_body_is_refused(served) -> None:
    server, _session, _api = served

    status, _body, _ = request(server, "POST", "/api/__claim", None, {
        "Content-Type": "application/json", "Content-Length": str(gui_server.MAX_BODY + 1),
    })

    assert status == 413


def test_the_server_stops_once_the_page_goes_quiet(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "index.html").write_text("x", encoding="utf-8")
    monkeypatch.setattr(gui_server, "IDLE_BEFORE_CLAIM", 0.2)
    api = FakeApi()
    lines: list = []
    started = time.monotonic()

    code = gui_server.serve(api, tmp_path, open_browser=False, announce=lines.append, poll=0.05)

    assert code == 0
    assert time.monotonic() - started < 5
    assert api.discarded
    assert "http://127.0.0.1:" in lines[0] and "#c=" in lines[0]
    assert any("ssh -L" in line for line in lines)
