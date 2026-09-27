"""Google Drive API calls: requests with retry, and the folder acg syncs through."""

import json
import random
import re
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

from . import gdrive_auth
from .config import (
    configured_gdrive_folder,
    configured_gdrive_folder_id,
    configured_gdrive_space,
)

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
_COMMIT_RE = re.compile(r"[0-9a-f]{40,64}")


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
    access_token = gdrive_auth.get_valid_access_token(environ)
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
                token_data = gdrive_auth.load_token(environ) or {}
                rf = token_data.get("refresh_token")
                if rf:
                    try:
                        new_token = gdrive_auth.refresh_access_token(rf, environ)
                        req_headers["Authorization"] = (
                            f"Bearer {new_token['access_token']}"
                        )
                        continue
                    except gdrive_auth.GDriveAuthError:
                        pass
                gdrive_auth.delete_token(environ)
                raise gdrive_auth.GDriveAuthError(
                    "Google Drive 授權已失效,請重新登入 (acg setup --provider gdrive)"
                ) from exc

            if exc.code in (403, 429) and retries < max_retries:
                retries += 1
                base_delay = 2 ** (retries - 1)
                jitter = random.uniform(0, 0.5)
                time.sleep(base_delay + jitter)
                continue

            raise gdrive_auth.GDriveError(
                f"Google Drive API error ({exc.code}): {body.decode('utf-8', errors='replace')}"
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            if retries < max_retries:
                retries += 1
                time.sleep(1 + random.uniform(0, 0.5))
                continue
            raise gdrive_auth.GDriveError(f"Network error calling Google Drive: {exc}") from exc


def _escape_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _require_folder_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise gdrive_auth.GDriveError(f"Google Drive did not return an id for folder {name!r}")
    return value


class GDriveClient:
    def __init__(
        self,
        environ: "dict[str, str] | None" = None,
        folder_path: "str | None" = None,
        folder_id: "str | None" = None,
        use_configured_id: bool = True,
        space: "str | None" = None,
    ) -> None:
        self.environ = environ
        self.space = space or configured_gdrive_space(environ)
        self.folder_path = folder_path or configured_gdrive_folder(environ)
        configured_id = (
            configured_gdrive_folder_id(environ) if use_configured_id else None
        )
        self._configured_folder_id = folder_id or configured_id
        self._folder_id: str | None = None

    @property
    def hidden(self) -> bool:
        return self.space == "hidden"

    def _list_files(self, query: str) -> list[dict[str, Any]]:
        query_params: dict[str, str] = {
            "q": query,
            "fields": "files(id, name, headRevisionId, modifiedTime)",
            "orderBy": "createdTime",
            "pageSize": 10,
        }
        if self.hidden:
            # 隱藏空間不在預設的搜尋範圍內,要明確指定 spaces
            query_params["spaces"] = gdrive_auth.APPDATA_SPACE
        params = urllib.parse.urlencode(query_params)
        url = f"{DRIVE_API_BASE}/files?{params}"
        _, _, body = make_drive_request(url, environ=self.environ)
        data = json.loads(body.decode("utf-8"))
        files = data.get("files", []) if isinstance(data, dict) else []
        return [item for item in files if isinstance(item, dict)]

    def _folder_still_exists(self, folder_id: str) -> bool:
        params = urllib.parse.urlencode({"fields": "id,trashed,mimeType"})
        url = f"{DRIVE_API_BASE}/files/{folder_id}?{params}"
        try:
            _, _, body = make_drive_request(url, environ=self.environ)
        except gdrive_auth.GDriveAuthError:
            raise
        except gdrive_auth.GDriveError:
            return False
        data = json.loads(body.decode("utf-8"))
        return (
            isinstance(data, dict)
            and data.get("id") == folder_id
            and not data.get("trashed")
            and data.get("mimeType") == FOLDER_MIME_TYPE
        )

    def _child_folder(self, parent_id: str, name: str) -> str:
        query = (
            f"name='{_escape_query_value(name)}' "
            f"and mimeType='{FOLDER_MIME_TYPE}' "
            f"and '{parent_id}' in parents and trashed=false"
        )
        folders = self._list_files(query)
        if folders:
            return _require_folder_id(folders[0].get("id"), name)
        return self._create_folder(parent_id, name)

    def _create_folder(self, parent_id: str, name: str) -> str:
        metadata = json.dumps(
            {
                "name": name,
                "mimeType": FOLDER_MIME_TYPE,
                "parents": [parent_id],
            }
        )
        url = f"{DRIVE_API_BASE}/files?fields=id"
        _, _, body = make_drive_request(
            url,
            method="POST",
            headers={"Content-Type": "application/json; charset=UTF-8"},
            data=metadata.encode("utf-8"),
            environ=self.environ,
        )
        created = json.loads(body.decode("utf-8"))
        folder_id = created.get("id") if isinstance(created, dict) else None
        return _require_folder_id(folder_id, name)

    def get_folder_id(self) -> str:
        """Return the parent that holds the synced files.

        In hidden mode that is the literal ``appDataFolder`` alias, which
        needs no lookup and cannot be renamed or moved by the user.

        The id saved at setup wins so the user may move or rename the folder
        in Drive afterwards. Only when that id is gone (folder trashed or
        deleted) is ``folder_path`` walked from My Drive root, creating each
        missing segment. drive.file only exposes folders this app created, so
        a hand-made folder with the same name is invisible here and a second
        one appears next to it.
        """
        if self.hidden:
            return gdrive_auth.APPDATA_SPACE
        if self._folder_id:
            return self._folder_id
        if self._configured_folder_id and self._folder_still_exists(
            self._configured_folder_id
        ):
            self._folder_id = self._configured_folder_id
            return self._folder_id
        parent_id = "root"
        for segment in self.folder_path.split("/"):
            parent_id = self._child_folder(parent_id, segment)
        self._folder_id = parent_id
        return parent_id

    def folder_url(self) -> str:
        """Link to the folder, or empty in hidden mode where none is visible."""
        if self.hidden:
            return ""
        return f"https://drive.google.com/drive/folders/{self.get_folder_id()}"

    def location_label(self) -> str:
        if self.hidden:
            return "Google 帳號的隱藏應用程式空間(appDataFolder)"
        return f"我的雲端硬碟/{self.folder_path}"

    def find_file(self, name: str) -> "dict[str, Any] | None":
        folder_id = self.get_folder_id()
        query = (
            f"name='{_escape_query_value(name)}' "
            f"and '{folder_id}' in parents and trashed=false"
        )
        files = self._list_files(query)
        return files[0] if files else None

    def get_file_metadata(self, file_id: str) -> dict[str, Any]:
        params = urllib.parse.urlencode(
            {
                "fields": "id,name,headRevisionId,modifiedTime",
            }
        )
        url = f"{DRIVE_API_BASE}/files/{file_id}?{params}"
        _, _, body = make_drive_request(url, environ=self.environ)
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            raise gdrive_auth.GDriveError("Google Drive returned invalid file metadata")
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
        metadata = json.dumps({"name": name, "parents": [self.get_folder_id()]})

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
            raise gdrive_auth.GDriveError("Google Drive head.json is not valid JSON") from exc
        if not isinstance(head_info, dict):
            raise gdrive_auth.GDriveError("Google Drive head.json must contain an object")
        commit = head_info.get("commit")
        if (
            not isinstance(commit, str)
            or _COMMIT_RE.fullmatch(commit) is None
            or head_info.get("format") != 1
        ):
            raise gdrive_auth.GDriveError("Google Drive head.json has an unsupported format")
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

    def verify_setup_access(self) -> str:
        """§1.5 Setup verification: create -> read back -> delete -> confirm vanished.

        Returns the URL of the ai-config folder so setup can show the user
        where the synced files live.
        """
        test_name = f"test_{secrets.token_hex(8)}.tmp"
        test_data = secrets.token_bytes(64)

        created = self.upload_file(test_name, test_data)
        file_id = created["id"]

        try:
            read_back = self.download_file_bytes(file_id)
            if read_back != test_data:
                raise gdrive_auth.GDriveError(
                    "Setup verification failed: downloaded content did not match uploaded content"
                )

            self.delete_file(file_id)

            found = self.find_file(test_name)
            if found is not None:
                raise gdrive_auth.GDriveError(
                    "Setup verification failed: test file was not permanently removed"
                )
        except gdrive_auth.GDriveError:
            try:
                self.delete_file(file_id)
            except gdrive_auth.GDriveError:
                pass
            raise
        return self.folder_url()
