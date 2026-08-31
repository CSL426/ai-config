"""Google Drive appDataFolder sync provider.

Provides OAuth 2.0 PKCE authentication and appDataFolder bundle transport
allowing users without GitHub accounts to sync their configuration repository
across devices via Google Drive.
"""

import base64
import hashlib
import http.server
import json
import os
import random
import re
import secrets
import socket
import socketserver
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import config_path
from .console import log_error, log_info, log_success

GDRIVE_CLIENT_ID = ""  # 開放原始碼儲存庫中預設為空字串,正式建置由 GitHub secret 注入
# Google 的 Desktop 類型 client 即使走 PKCE,token 交換仍要求 client_secret;
# 官方文件明言桌面應用的 secret「並非機密」但必須附上(gcloud/rclone 同做法)。
GDRIVE_CLIENT_SECRET = ""
GDRIVE_SCOPE = "https://www.googleapis.com/auth/drive.appdata"
OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
TOKEN_FILE_NAME = "gdrive_token.json"
_COMMIT_RE = re.compile(r"[0-9a-f]{40,64}")


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
    return (
        environment.get("AI_CONFIG_GDRIVE_CLIENT_SECRET") or GDRIVE_CLIENT_SECRET
    )


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
    challenge = (
        base64.urlsafe_b64encode(digest)
        .decode("ascii")
        .rstrip("=")
    )
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
) -> dict[str, Any]:
    client_id = get_client_id(environ)
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
        "scope": GDRIVE_SCOPE,
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
    token_data = {
        "access_token": access_token,
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": int(time.time()) + int(data.get("expires_in", 3600)),
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
            "Google Drive 授權已失效或過期,請重新登入 "
            "(acg setup --provider gdrive)"
        )
    token_data = {
        "access_token": access_token,
        "refresh_token": data.get("refresh_token") or refresh_token,
        "expires_at": int(time.time()) + int(data.get("expires_in", 3600)),
    }
    save_token(token_data, environ)
    return token_data


def get_valid_access_token(
    environ: "dict[str, str] | None" = None,
) -> str:
    token_data = load_token(environ)
    if not token_data or "access_token" not in token_data:
        raise GDriveAuthError("尚未登入 Google Drive,請先執行 acg setup --provider gdrive")

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


def make_drive_request(
    url: str,
    method: str = "GET",
    headers: "dict[str, str] | None" = None,
    data: "bytes | None" = None,
    environ: "dict[str, str] | None" = None,
) -> tuple[int, dict[str, str], bytes]:
    """Execute authenticated Google Drive HTTP request with exponential backoff.

    Retries on 403 / 429 errors up to 3 times with exponential backoff and jitter.
    Refreshes access token once on 401.
    """
    req_headers = dict(headers or {})
    access_token = get_valid_access_token(environ)
    req_headers["Authorization"] = f"Bearer {access_token}"

    retries = 0
    max_retries = 3
    token_refreshed = False

    while True:
        req = urllib.request.Request(
            url,
            data=data,
            headers=req_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp_headers = dict(resp.headers)
                body = resp.read()
                return resp.status, resp_headers, body
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if exc.code == 401 and not token_refreshed:
                token_refreshed = True
                token_data = load_token(environ) or {}
                rf = token_data.get("refresh_token")
                if rf:
                    try:
                        new_token = refresh_access_token(rf, environ)
                        req_headers["Authorization"] = f"Bearer {new_token['access_token']}"
                        continue
                    except GDriveAuthError:
                        pass
                delete_token(environ)
                raise GDriveAuthError(
                    "Google Drive 授權已失效,請重新登入 (acg setup --provider gdrive)"
                ) from exc

            if exc.code in (403, 429) and retries < max_retries:
                retries += 1
                base_delay = 2 ** (retries - 1)
                jitter = random.uniform(0, 0.5)
                time.sleep(base_delay + jitter)
                continue

            raise GDriveError(
                f"Google Drive API error ({exc.code}): {body.decode('utf-8', errors='replace')}"
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            if retries < max_retries:
                retries += 1
                time.sleep(1 + random.uniform(0, 0.5))
                continue
            raise GDriveError(f"Network error calling Google Drive: {exc}") from exc


class GDriveClient:
    def __init__(self, environ: "dict[str, str] | None" = None) -> None:
        self.environ = environ

    def find_file(self, name: str) -> "dict[str, Any] | None":
        escaped_name = name.replace("\\", "\\\\").replace("'", "\\'")
        query = f"name='{escaped_name}' and trashed=false"
        params = urllib.parse.urlencode({
            "spaces": "appDataFolder",
            "q": query,
            "fields": "files(id, name, headRevisionId, modifiedTime)",
        })
        url = f"{DRIVE_API_BASE}/files?{params}"
        _, _, body = make_drive_request(url, environ=self.environ)
        data = json.loads(body.decode("utf-8"))
        files = data.get("files", [])
        return files[0] if files else None

    def get_file_metadata(self, file_id: str) -> dict[str, Any]:
        params = urllib.parse.urlencode({
            "fields": "id,name,headRevisionId,modifiedTime",
        })
        url = f"{DRIVE_API_BASE}/files/{file_id}?{params}"
        _, _, body = make_drive_request(url, environ=self.environ)
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            raise GDriveError("Google Drive returned invalid file metadata")
        return data

    def download_file_bytes(self, file_id: str) -> bytes:
        url = f"{DRIVE_API_BASE}/files/{file_id}?alt=media"
        _, _, body = make_drive_request(url, environ=self.environ)
        return body

    def upload_file(
        self,
        name: str,
        content: bytes,
        file_id: "str | None" = None,
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        existing = self.find_file(name) if file_id is None else None
        target_file_id = file_id or (existing["id"] if existing else None)

        if target_file_id:
            url = f"{DRIVE_UPLOAD_BASE}/files/{target_file_id}?uploadType=media&fields=id,name,headRevisionId"
            headers = {"Content-Type": content_type}
            _, _, body = make_drive_request(
                url,
                method="PATCH",
                headers=headers,
                data=content,
                environ=self.environ,
            )
            return json.loads(body.decode("utf-8"))

        boundary = f"=====boundary_{secrets.token_hex(8)}====="
        metadata = json.dumps({"name": name, "parents": ["appDataFolder"]})

        parts = [
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{metadata}\r\n".encode(),
            f"--{boundary}\r\nContent-Type: {content_type}\r\n\r\n".encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        body_data = b"".join(parts)
        headers = {"Content-Type": f"multipart/related; boundary={boundary}"}

        url = f"{DRIVE_UPLOAD_BASE}/files?uploadType=multipart&fields=id,name,headRevisionId"
        _, _, body = make_drive_request(
            url,
            method="POST",
            headers=headers,
            data=body_data,
            environ=self.environ,
        )
        return json.loads(body.decode("utf-8"))

    def delete_file(self, file_id: str) -> None:
        url = f"{DRIVE_API_BASE}/files/{file_id}"
        make_drive_request(url, method="DELETE", environ=self.environ)

    def get_head_info(self) -> "dict[str, Any] | None":
        head_file = self.find_file("head.json")
        if not head_file:
            return None
        content = self.download_file_bytes(head_file["id"])
        try:
            head_info = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise GDriveError("Google Drive head.json is not valid JSON") from exc
        if not isinstance(head_info, dict):
            raise GDriveError("Google Drive head.json must contain an object")
        commit = head_info.get("commit")
        if (
            not isinstance(commit, str)
            or _COMMIT_RE.fullmatch(commit) is None
            or head_info.get("format") != 1
        ):
            raise GDriveError("Google Drive head.json has an unsupported format")
        return head_info

    def update_head_info(self, commit_sha: str) -> dict[str, Any]:
        head_data = {
            "commit": commit_sha,
            "updated_at": datetime.now(UTC).isoformat(),
            "device": socket.gethostname(),
            "format": 1,
        }
        content = json.dumps(head_data, ensure_ascii=False, indent=2).encode("utf-8")
        return self.upload_file("head.json", content, content_type="application/json")

    def verify_setup_access(self) -> None:
        """§1.5 Setup verification: create -> read back -> delete -> confirm vanished."""
        test_name = f"test_{secrets.token_hex(8)}.tmp"
        test_data = secrets.token_bytes(64)

        created = self.upload_file(test_name, test_data)
        file_id = created["id"]

        try:
            read_back = self.download_file_bytes(file_id)
            if read_back != test_data:
                raise GDriveError(
                    "Setup verification failed: downloaded content did not match uploaded content"
                )

            self.delete_file(file_id)

            found = self.find_file(test_name)
            if found is not None:
                raise GDriveError(
                    "Setup verification failed: test file was not permanently removed"
                )
        except GDriveError:
            try:
                self.delete_file(file_id)
            except GDriveError:
                pass
            raise


def gdrive_pull(repo_dir: Path, tool: str) -> int:
    """§1.3 pull implementation for gdrive provider."""
    from .commands.status import show_status
    from .commands.sync import _git_failure, _repository_operation, _run_repo_git
    from .console import log_header, log_success

    log_header("Sync repository changes (Google Drive)")

    operation = _repository_operation(repo_dir)
    if operation is not None:
        if operation != "<invalid>":
            log_error(f"Data repository has a {operation} in progress; pull cancelled.")
        return 1

    status = _run_repo_git(
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        repo_dir=repo_dir,
    )
    if status.returncode != 0:
        _git_failure("Reading repository status", status)
        return 1
    if status.stdout.strip():
        log_error("Data repository has uncommitted changes; pull cancelled.")
        print(status.stdout.rstrip())
        return 1

    branch = _run_repo_git(
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        repo_dir=repo_dir,
    )
    if branch.returncode != 0:
        log_error("Data repository is in detached HEAD state; pull cancelled.")
        return 1
    if branch.stdout.strip() != "main":
        log_error("Google Drive data repository must use the main branch.")
        return 1

    client = GDriveClient()
    head_info = client.get_head_info()

    if not head_info or "commit" not in head_info:
        log_error("遠端為空,請先執行 acg push 上傳設定。")
        return 1

    remote_commit = head_info["commit"]
    local_head_proc = _run_repo_git(
        "rev-parse",
        "--verify",
        "HEAD",
        repo_dir=repo_dir,
    )
    local_head = (
        local_head_proc.stdout.strip()
        if local_head_proc.returncode == 0
        else None
    )

    if remote_commit == local_head:
        log_success("Data repository is already up to date")
        print()
        show_status(tool)
        print()
        log_info("Run acg apply to deploy")
        return 0

    bundle_file = client.find_file("repo.bundle")
    if not bundle_file:
        log_error("Google Drive 上找不到 repo.bundle 檔案")
        return 1

    bundle_bytes = client.download_file_bytes(bundle_file["id"])

    with tempfile.NamedTemporaryFile("wb", suffix=".bundle", delete=False) as tmp:
        tmp.write(bundle_bytes)
        tmp_path = Path(tmp.name)

    try:
        verify = _run_repo_git(
            "bundle",
            "verify",
            str(tmp_path),
            repo_dir=repo_dir,
        )
        if verify.returncode != 0:
            _git_failure("Verifying downloaded repository bundle", verify)
            return 1

        fetch = _run_repo_git(
            "fetch",
            str(tmp_path),
            "main",
            repo_dir=repo_dir,
        )
        if fetch.returncode != 0:
            fetch = _run_repo_git(
                "fetch",
                str(tmp_path),
                "HEAD",
                repo_dir=repo_dir,
            )
            if fetch.returncode != 0:
                _git_failure("Fetching updates from Google Drive bundle", fetch)
                return 1

        fetched_head = _run_repo_git("rev-parse", "FETCH_HEAD", repo_dir=repo_dir)
        if (
            fetched_head.returncode != 0
            or fetched_head.stdout.strip() != remote_commit
        ):
            log_error("Google Drive repo.bundle does not match head.json; pull cancelled.")
            return 1

        merge_ff = _run_repo_git(
            "merge",
            "--ff-only",
            "FETCH_HEAD",
            repo_dir=repo_dir,
        )
        if merge_ff.returncode != 0:
            log_error(
                "Data repository is not safe to fast-forward; pull cancelled. "
                "本機有未上傳的提交,先 push 或手動處理。"
            )
            return 1

        log_success("Data repository fast-forwarded from Google Drive")
    finally:
        tmp_path.unlink(missing_ok=True)

    print()
    show_status(tool)
    print()
    log_info("Run acg apply to deploy")
    return 0


def gdrive_push_upload(repo_dir: Path) -> int:
    """Upload a full bundle while narrowing Drive's non-atomic CAS window.

    Google Drive has no atomic compare-and-swap across repo.bundle and
    head.json. Re-reading repo.bundle's revision after upload detects a
    competing write before this client publishes head.json, but cannot make
    the two-file update fully atomic.
    """
    try:
        return _gdrive_push_upload(repo_dir)
    except (GDriveError, OSError) as exc:
        log_error(f"Google Drive upload failed: {exc}")
        return 1


def _gdrive_push_upload(repo_dir: Path) -> int:
    from .commands.sync import _git_failure, _run_repo_git
    from .console import log_success

    branch = _run_repo_git(
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        repo_dir=repo_dir,
    )
    if branch.returncode != 0 or branch.stdout.strip() != "main":
        log_error("Google Drive data repository must use the main branch.")
        return 1

    local_head_proc = _run_repo_git("rev-parse", "HEAD", repo_dir=repo_dir)
    if local_head_proc.returncode != 0:
        _git_failure("Reading local HEAD", local_head_proc)
        return 1
    local_head = local_head_proc.stdout.strip()

    client = GDriveClient()

    head_info = client.get_head_info()
    if head_info and "commit" in head_info:
        remote_commit = head_info["commit"]
        if remote_commit != local_head:
            ancestor_check = _run_repo_git(
                "merge-base",
                "--is-ancestor",
                remote_commit,
                local_head,
                repo_dir=repo_dir,
            )
            if ancestor_check.returncode != 0:
                log_error(
                    "Data repository has diverged from Google Drive; push cancelled. "
                    "遠端有較新的提交,請先執行 pull。"
                )
                return 1

    with tempfile.NamedTemporaryFile("wb", suffix=".bundle", delete=False) as tmp:
        tmp_bundle_path = Path(tmp.name)

    try:
        bundle_create = _run_repo_git(
            "bundle",
            "create",
            str(tmp_bundle_path),
            "main",
            repo_dir=repo_dir,
        )
        if bundle_create.returncode != 0:
            _git_failure("Creating repository bundle", bundle_create)
            return 1

        bundle_content = tmp_bundle_path.read_bytes()

        existing_bundle = client.find_file("repo.bundle")
        old_revision = existing_bundle.get("headRevisionId") if existing_bundle else None

        res = client.upload_file(
            "repo.bundle",
            bundle_content,
            file_id=existing_bundle.get("id") if existing_bundle else None,
        )
        uploaded_file_id = res.get("id")
        new_revision = res.get("headRevisionId")

        if (
            not isinstance(uploaded_file_id, str)
            or not isinstance(new_revision, str)
            or not new_revision
            or (old_revision is not None and old_revision == new_revision)
        ):
            log_error("Revision mismatch during Google Drive upload")
            return 1

        observed = client.get_file_metadata(uploaded_file_id)
        if observed.get("headRevisionId") != new_revision:
            log_error("Revision mismatch during Google Drive upload")
            return 1

        client.update_head_info(local_head)
        log_success("Local configuration committed and uploaded to Google Drive")
        return 0
    finally:
        tmp_bundle_path.unlink(missing_ok=True)
