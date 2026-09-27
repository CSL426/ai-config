"""Google Drive sign-in: OAuth 2.0 with PKCE, and the token kept on disk.

Users without a GitHub account sync their data repository through an
``ai-config`` folder in their My Drive; this is how acg gets permission.
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import socketserver
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from .config import config_path, configured_gdrive_space
from .console import log_info, log_success

GDRIVE_CLIENT_ID = ""  # 開放原始碼儲存庫中預設為空字串,正式建置由 GitHub secret 注入
# Google 的 Desktop 類型 client 即使走 PKCE,token 交換仍要求 client_secret;
# 官方文件明言桌面應用的 secret「並非機密」但必須附上(gcloud/rclone 同做法)。
GDRIVE_CLIENT_SECRET = ""
# drive.file 只能看到本應用自己建立的檔案,權限最小;但檔案放在一般「我的雲端硬碟」
# 底下,使用者在 Drive 網頁/桌面版都看得到(早期版本用 appDataFolder,永遠隱藏)。
# 兩種儲存位置各自對應最小的 scope:
# visible = drive.file,檔案在「我的雲端硬碟」底下,使用者看得到也搬得動;
# hidden  = drive.appdata,Google 給應用程式的隱藏空間,Drive 介面永遠看不到。
# 兩者都只能存取本程式自己建立的檔案,碰不到使用者其他的 Drive 內容。
SCOPE_VISIBLE = "https://www.googleapis.com/auth/drive.file"
SCOPE_HIDDEN = "https://www.googleapis.com/auth/drive.appdata"
GDRIVE_SCOPE = SCOPE_VISIBLE  # 預設;實際使用一律走 scope_for_space()
APPDATA_SPACE = "appDataFolder"


def scope_for_space(space: str) -> str:
    return SCOPE_HIDDEN if space == "hidden" else SCOPE_VISIBLE


OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKEN_FILE_NAME = "gdrive_token.json"


class GDriveError(RuntimeError):
    """Base exception for Google Drive operations."""


class GDriveAuthError(GDriveError):
    """Raised when authentication or token refresh fails."""


def get_client_id(environ: "dict[str, str] | None" = None) -> str:
    environment = os.environ if environ is None else environ
    client_id = environment.get("AI_CONFIG_GDRIVE_CLIENT_ID") or GDRIVE_CLIENT_ID
    if not client_id:
        raise GDriveAuthError(
            "此建置未包含 Google 登入,請設定 AI_CONFIG_GDRIVE_CLIENT_ID 環境變數"
        )
    return client_id


def get_client_secret(environ: "dict[str, str] | None" = None) -> str:
    environment = os.environ if environ is None else environ
    return environment.get("AI_CONFIG_GDRIVE_CLIENT_SECRET") or GDRIVE_CLIENT_SECRET


def token_file_path(environ: "dict[str, str] | None" = None) -> Path:
    return config_path(environ).parent / TOKEN_FILE_NAME


def load_token(environ: "dict[str, str] | None" = None) -> "dict[str, Any] | None":
    path = token_file_path(environ)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return None


def save_token(
    token_data: dict[str, Any],
    environ: "dict[str, str] | None" = None,
) -> Path:
    path = token_file_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(token_data, ensure_ascii=False, indent=2)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.write("\n")
            temporary_path = Path(temporary.name)
        if os.name != "nt":
            temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise GDriveError(f"Cannot save Google Drive token: {exc}") from exc
    return path


def delete_token(environ: "dict[str, str] | None" = None) -> None:
    path = token_file_path(environ)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


class _OAuthRedirectHandler(http.server.BaseHTTPRequestHandler):
    auth_code: "str | None" = None
    auth_error: "str | None" = None
    expected_state: str = ""

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        state = query.get("state", [""])[0]
        code = query.get("code", [""])[0]
        error = query.get("error", [""])[0]

        if state != _OAuthRedirectHandler.expected_state:
            _OAuthRedirectHandler.auth_error = "OAuth state mismatch"
            self._respond(400, "State mismatch. Please try again.")
            return

        if error:
            _OAuthRedirectHandler.auth_error = f"Authorization denied: {error}"
            self._respond(400, f"Authorization failed: {error}")
            return

        if code:
            _OAuthRedirectHandler.auth_code = code
            self._respond(
                200,
                "<!doctype html><html><head><meta charset='utf-8'></head><body>"
                "<h2>登入成功!</h2>"
                "<p>已成功連結 Google 帳號,請回到 acg 繼續。</p>"
                "</body></html>",
            )
            return

        self._respond(400, "Invalid request")

    def _respond(self, code: int, body_html: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body_html.encode("utf-8"))

    def log_message(self, format_str: str, *args: Any) -> None:
        # Silence local loopback server HTTP access logs
        pass


def run_oauth_flow(
    timeout: float = 120.0,
    environ: "dict[str, str] | None" = None,
    space: "str | None" = None,
) -> dict[str, Any]:
    client_id = get_client_id(environ)
    wanted_scope = scope_for_space(space or configured_gdrive_space(environ))
    verifier, challenge = generate_pkce()
    state = secrets.token_hex(16)

    _OAuthRedirectHandler.auth_code = None
    _OAuthRedirectHandler.auth_error = None
    _OAuthRedirectHandler.expected_state = state

    server = socketserver.TCPServer(("127.0.0.1", 0), _OAuthRedirectHandler)
    server.timeout = min(1.0, timeout)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": wanted_scope,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    auth_url = f"{OAUTH_AUTH_URL}?{urllib.parse.urlencode(params)}"

    try:
        log_info("已開啟瀏覽器進行 Google 帳號授權…")
        webbrowser.open(auth_url)

        start_time = time.monotonic()
        while time.monotonic() - start_time < timeout:
            server.handle_request()
            if _OAuthRedirectHandler.auth_code or _OAuthRedirectHandler.auth_error:
                break
    finally:
        server.server_close()

    if _OAuthRedirectHandler.auth_error:
        raise GDriveAuthError(_OAuthRedirectHandler.auth_error)
    if not _OAuthRedirectHandler.auth_code:
        raise GDriveAuthError("Google 授權逾時,請重試")

    token_params = {
        "client_id": client_id,
        "code": _OAuthRedirectHandler.auth_code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    client_secret = get_client_secret(environ)
    if client_secret:
        token_params["client_secret"] = client_secret

    req = urllib.request.Request(
        OAUTH_TOKEN_URL,
        data=urllib.parse.urlencode(token_params).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        hint = ""
        if "client_secret" in detail:
            hint = (
                "\nGoogle 的桌面型 client 需要 client secret:請設定 "
                "AI_CONFIG_GDRIVE_CLIENT_SECRET 環境變數(或使用內建它的正式建置)"
            )
        raise GDriveAuthError(
            f"OAuth token exchange failed ({exc.code}): {detail}{hint}"
        ) from exc
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        raise GDriveAuthError(f"OAuth token exchange failed: {exc}") from exc

    access_token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(access_token, str) or not access_token:
        raise GDriveAuthError("OAuth token exchange returned no access token")
    # Google 的同意畫面允許使用者逐項取消勾選,拿到 token 不代表拿到 Drive 權限
    granted_scope = str(data.get("scope") or wanted_scope)
    if wanted_scope not in granted_scope.split():
        raise GDriveAuthError(
            "Google 帳號未授予 Drive 檔案權限,請重新登入並勾選允許存取"
        )
    token_data = {
        "access_token": access_token,
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": int(time.time()) + int(data.get("expires_in", 3600)),
        "scope": granted_scope,
    }
    save_token(token_data, environ)
    log_success("Google 帳號授權成功")
    return token_data


def refresh_access_token(
    refresh_token: str,
    environ: "dict[str, str] | None" = None,
) -> dict[str, Any]:
    client_id = get_client_id(environ)
    token_params = {
        "client_id": client_id,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    client_secret = get_client_secret(environ)
    if client_secret:
        token_params["client_secret"] = client_secret

    req = urllib.request.Request(
        OAUTH_TOKEN_URL,
        data=urllib.parse.urlencode(token_params).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        delete_token(environ)
        raise GDriveAuthError(
            "Google Drive 授權已失效或過期,請重新登入 (acg setup --provider gdrive)"
        ) from exc
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        raise GDriveError(f"Token refresh request failed: {exc}") from exc

    access_token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(access_token, str) or not access_token:
        delete_token(environ)
        raise GDriveAuthError(
            "Google Drive 授權已失效或過期,請重新登入 (acg setup --provider gdrive)"
        )
    previous = load_token(environ) or {}
    token_data = {
        "access_token": access_token,
        "refresh_token": data.get("refresh_token") or refresh_token,
        "expires_at": int(time.time()) + int(data.get("expires_in", 3600)),
        "scope": str(data.get("scope") or previous.get("scope") or ""),
    }
    save_token(token_data, environ)
    return token_data


def token_has_scope(
    token_data: dict[str, Any],
    environ: "dict[str, str] | None" = None,
) -> bool:
    wanted = scope_for_space(configured_gdrive_space(environ))
    return wanted in str(token_data.get("scope", "")).split()


def get_valid_access_token(
    environ: "dict[str, str] | None" = None,
) -> str:
    token_data = load_token(environ)
    if not token_data or "access_token" not in token_data:
        raise GDriveAuthError(
            "尚未登入 Google Drive,請先執行 acg setup --provider gdrive"
        )
    if not token_has_scope(token_data, environ):
        # 換儲存位置就換 scope,舊 token 對新位置的 API 一律 403
        delete_token(environ)
        raise GDriveAuthError(
            "Google Drive 授權範圍與目前的儲存位置設定不符,"
            "請重新登入 (acg setup --provider gdrive)"
        )

    expires_at = token_data.get("expires_at", 0)
    if time.time() >= expires_at - 60:
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            delete_token(environ)
            raise GDriveAuthError(
                "Google Drive 授權已過期,請重新登入 (acg setup --provider gdrive)"
            )
        token_data = refresh_access_token(refresh_token, environ)

    return token_data["access_token"]
