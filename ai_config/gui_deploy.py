"""The desktop app's project deploy panel: pick a folder, preview, confirm.

Same shape as the other management actions: the folder chosen through
select_project comes back as a token instead of a path the page could
forge, a preview hands back a token too, and confirming recomputes the
preview first so nothing runs that the person did not see.
"""

import contextlib
import io
import re
import secrets

from . import paths
from .applyplan import StalePreview
from .commands import deploy
from .gui_management import failure, outcome
from .locking import apply_lock

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class DeployApi:
    def _init_deploy(self):
        self._deploy_project = None

    def _deploy_path(self, token):
        chosen = getattr(self, "_deploy_project", None)
        if not isinstance(token, str) or not chosen:
            raise StalePreview("請先選擇專案資料夾")
        expected, root, repository = chosen
        if (not secrets.compare_digest(token, expected)
                or str(paths.SCRIPT_DIR) != repository or not root.is_dir()):
            raise StalePreview("專案資料夾或資料庫已變動,請重新選擇")
        return root

    def deploy_info(self, project_token=None):
        empty = {"items": [], "deployed": None, "root": None}
        try:
            self._ensure_configured()
            listed = [{"name": item.name, "note": item.note, "kind": deploy.kind(item)}
                      for item in deploy.items()]
            if project_token is None:
                return {**empty, **outcome(), "items": listed}
            root = self._deploy_path(project_token)
            return {**empty, **outcome(), "items": listed, "root": str(root),
                    "deployed": deploy.summary(root)}
        except (OSError, RuntimeError, ValueError) as exc:
            return {**empty, **failure(exc)}

    def _deploy_preview(self, kind, project_token, names=None):
        empty = {"token": "", "needs_confirmation": False, "scope": {}, "changes": [], "warnings": []}
        if not self._lock.acquire(blocking=False):
            return {**empty, **outcome(1, "另一個動作正在執行", "BUSY")}
        try:
            self._discard_previews()
            self._ensure_configured()
            root = self._deploy_path(project_token)
            if kind == "deploy":
                if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                    raise ValueError("無效的選取")
                changes = deploy.preview(root, names)
            else:
                changes = deploy.removal_preview(root)
            scope = {"action": kind, "project_key": str(root)}
            if not any(change["operation"] != "skip" for change in changes):
                return {**empty, **outcome(output="已一致"), "scope": scope, "changes": changes}
            token = secrets.token_urlsafe(24)
            self._pending = {"kind": kind, "token": token, "names": names,
                             "project_token": None, "deploy_token": project_token,
                             "changes": changes, "repo": str(paths.SCRIPT_DIR)}
            lines = "\n".join(f"{c['operation']} {c['destination']} — {c['reason']}" for c in changes)
            return {**empty, **outcome(output=lines), "token": token, "needs_confirmation": True,
                    "scope": scope, "changes": changes}
        except (OSError, RuntimeError, ValueError) as exc:
            return {**empty, **failure(exc)}
        finally:
            self._lock.release()

    def preview_deploy(self, project_token, names):
        return self._deploy_preview("deploy", project_token, names)

    def preview_undeploy(self, project_token):
        return self._deploy_preview("undeploy", project_token)

    def _deploy_confirm(self, kind, token):
        if not self._lock.acquire(blocking=False):
            return outcome(1, "另一個動作正在執行", "BUSY")
        pending = None
        try:
            pending = self._pending
            if (not isinstance(token, str) or not pending or pending["kind"] != kind
                    or not secrets.compare_digest(token, pending["token"])):
                raise StalePreview("預覽已失效,請重新預覽")
            self._pending = None
            if str(paths.SCRIPT_DIR) != pending["repo"]:
                raise StalePreview("資料庫已切換,請重新預覽")
            root = self._deploy_path(pending["deploy_token"])
            with apply_lock(timeout=0):
                # 預覽之後專案或資料庫若有變動,就不照舊的預覽動手
                fresh = (deploy.preview(root, pending["names"]) if kind == "deploy"
                         else deploy.removal_preview(root))
                if fresh != pending["changes"]:
                    raise StalePreview("內容在預覽後已有變動,請重新預覽")
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                    if kind == "deploy":
                        code = deploy.execute(root, pending["names"])
                    else:
                        from . import deploy_record

                        code = deploy.remove(root, deploy_record.load(root))
            return outcome(code, _ANSI_RE.sub("", buffer.getvalue()))
        except TimeoutError as exc:
            # 還沒拿到鎖就還沒動手;預覽仍然有效
            if pending:
                self._pending = pending
            return failure(exc)
        except (OSError, RuntimeError, ValueError) as exc:
            return failure(exc)
        finally:
            self._lock.release()

    def confirm_deploy(self, token):
        return self._deploy_confirm("deploy", token)

    def confirm_undeploy(self, token):
        return self._deploy_confirm("undeploy", token)
