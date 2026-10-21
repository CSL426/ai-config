"""Step aside local edits that the incoming commits already contain.

Two machines often make the same change on their own: each archives the
same stale handoff, or writes the same file. The second one to sync then
has an unsaved copy in the way of the identical incoming one, git refuses
the fast-forward or rebase, and the advice to push first cannot work on a
branch that is behind: a deadlock nobody caused. A local edit whose result
is already what the remote brings loses nothing by stepping aside.

Content is compared the way git stores it (``hash-object --path`` applies
the path's end-of-line rules), so a CRLF checkout of an LF blob on Windows
counts as the same file.
"""

import os
from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess

Git = Callable[..., "CompletedProcess[str] | None"]


def _ok(result: "CompletedProcess[str] | None") -> bool:
    return result is not None and result.returncode == 0


def _upstream_blob(git: Git, upstream: str, path: str) -> "str | None":
    result = git("rev-parse", "--verify", "--quiet", f"{upstream}:{path}")
    return result.stdout.strip() if _ok(result) else None


def _worktree_blob(git: Git, path: str) -> "str | None":
    result = git("hash-object", f"--path={path}", "--", path)
    return result.stdout.strip() if _ok(result) else None


def clear_identical_incoming(git: Git, root: Path, upstream: str = "@{upstream}") -> "list[str]":
    """Undo unsaved edits the incoming commits make identically; return their paths.

    Only paths the incoming range touches are looked at, and only edits
    that are not staged: an untracked file the remote adds with the same
    content is removed, a tracked file the remote also deletes is restored
    so the merge deletes it, and a modified file the remote changes to the
    same content is restored so the merge writes it. If any local edit to
    such a path differs, the merge would be refused anyway, so nothing is
    touched at all.
    """
    # 只看遠端自己改了什麼:從分岔點比,本機已 commit 的改動不會被當成遠端的
    base = git("merge-base", "HEAD", upstream)
    if not _ok(base):
        return []
    incoming = git("diff", "--name-status", "--no-renames", "-z", base.stdout.strip(), upstream)
    status = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    if not _ok(incoming) or not _ok(status):
        return []
    fields = incoming.stdout.split("\0")
    remote = {path: kind for kind, path in zip(fields[0::2], fields[1::2]) if path}
    actions: list = []
    entries = iter(status.stdout.split("\0"))
    for entry in entries:
        code, path = entry[:2], entry[3:]
        if code[:1] in ("R", "C"):
            next(entries, None)  # 已暫存的改名多帶一欄原路徑
        kind = remote.get(path)
        if not path or kind is None:
            continue  # 遠端沒碰的檔案擋不住合併
        action = _step_aside(git, upstream, code, kind, path)
        if action is None:
            # 有一個真的不同,合併照樣會被擋;這時什麼都不碰,工作區維持原樣
            return []
        actions.append((action, path))
    for action, path in actions:
        if action == "remove":
            os.remove(root / path)
        elif not _ok(git("checkout", "--", path)):
            return [done for _kind, done in actions[:actions.index((action, path))]]
    return [path for _action, path in actions]


def _step_aside(git: Git, upstream: str, code: str, kind: str, path: str) -> "str | None":
    """How to make room for the incoming change at path, or None if it differs."""
    if code == "??" and kind == "A":
        blob = _upstream_blob(git, upstream, path)
        return "remove" if blob and blob == _worktree_blob(git, path) else None
    if code == " D" and kind == "D":
        return "restore"
    if code == " M" and kind == "M":
        blob = _upstream_blob(git, upstream, path)
        return "restore" if blob and blob == _worktree_blob(git, path) else None
    return None
