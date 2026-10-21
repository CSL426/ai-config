"""Step aside local edits that the incoming commits already contain.

Two machines often make the same change on their own: each archives the
same stale handoff, or writes the same file. The second one to sync then
has an unsaved copy in the way of the identical incoming one, git refuses
the fast-forward or rebase, and the advice to push first cannot work on a
branch that is behind: a deadlock nobody caused. A local edit whose result
is already what the remote brings loses nothing by stepping aside, and is
put back if the merge fails anyway.

Bytes are compared directly, with CRLF allowed to stand for LF (a Windows
checkout of an LF blob). `git hash-object --path` would normalize the same
way but also runs any clean filter configured for the path, and the path
comes from the remote.
"""

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


def _git(root: Path, *args: str) -> "subprocess.CompletedProcess[bytes] | None":
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, check=False, timeout=60,
            # 路徑來自遠端:當成字面路徑,不讓 * ? [ 變成萬用字元波及別的檔案
            env={**os.environ, "GIT_LITERAL_PATHSPECS": "1", "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _ok(result: "subprocess.CompletedProcess[bytes] | None") -> bool:
    return result is not None and result.returncode == 0


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


@dataclass
class _Step:
    action: str  # remove | restore-deleted | restore-modified
    path: str
    content: bytes = b""
    mode: int = 0


@dataclass
class StepAside:
    """What was moved out of the way, and how to put it back."""

    root: Path
    steps: list = field(default_factory=list)

    @property
    def paths(self) -> list:
        return [step.path for step in self.steps]

    def undo(self) -> None:
        """The merge failed after all: leave the worktree as it was found."""
        for step in reversed(self.steps):
            target = self.root / step.path
            try:
                if step.action == "restore-deleted":
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(step.content)
                    os.chmod(target, step.mode)
            except OSError:
                continue
        self.steps = []


def _incoming_entry(root: Path, upstream: str, path: str) -> "tuple[int, bytes] | None":
    """The incoming file's mode and content, if it is a plain or executable file."""
    listed = _git(root, "ls-tree", "-z", upstream, "--", path)
    if not _ok(listed):
        return None
    for record in listed.stdout.split(b"\0"):
        meta, _, name = record.partition(b"\t")
        parts = meta.split()
        if _text(name) != path or len(parts) != 3 or parts[1] != b"blob":
            continue
        if parts[0] not in (b"100644", b"100755"):
            return None  # symlink 或 submodule 不比
        blob = _git(root, "cat-file", "blob", _text(parts[2]))
        return (int(parts[0], 8), blob.stdout) if _ok(blob) else None
    return None


def _matches(root: Path, path: str, entry: "tuple[int, bytes] | None") -> "_Step | None":
    target = root / path
    if entry is None or target.is_symlink() or not target.is_file():
        return None
    mode, blob = entry
    stat = target.stat()
    # Windows 沒有可執行位元,git 也不記;其他平台位元不同就不算一樣
    if os.name != "nt" and bool(stat.st_mode & 0o111) != bool(mode & 0o111):
        return None
    content = target.read_bytes()
    if content != blob and content.replace(b"\r\n", b"\n") != blob:
        return None
    return _Step("", path, content, stat.st_mode & 0o7777)


def _plan(root: Path, upstream: str) -> "list[_Step] | None":
    base = _git(root, "merge-base", "HEAD", upstream)
    if not _ok(base):
        return None
    # 只看遠端自己改了什麼:從分岔點比,本機已 commit 的改動不會被當成遠端的
    incoming = _git(root, "diff", "--name-status", "--no-renames", "-z", _text(base.stdout).strip(), upstream)
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if not _ok(incoming) or not _ok(status):
        return None
    fields = [_text(part) for part in incoming.stdout.split(b"\0")]
    remote = {path: kind for kind, path in zip(fields[0::2], fields[1::2]) if path}
    steps = []
    entries = iter(_text(status.stdout).split("\0"))
    for entry in entries:
        code, path = entry[:2], entry[3:]
        if code[:1] in ("R", "C"):
            next(entries, None)  # 已暫存的改名多帶一欄原路徑
        kind = remote.get(path)
        if not path or kind is None:
            continue  # 遠端沒碰的檔案擋不住合併
        if code == " D" and kind == "D":
            steps.append(_Step("restore-deleted", path))
            continue
        action = {("??", "A"): "remove", (" M", "M"): "restore-modified"}.get((code, kind))
        step = _matches(root, path, _incoming_entry(root, upstream, path)) if action else None
        if step is None:
            # 有一個真的不同,合併照樣會被擋;這時什麼都不碰,工作區維持原樣
            return None
        step.action = action
        steps.append(step)
    return steps


def step_aside_identical(root: Path, upstream: str) -> StepAside:
    """Move unsaved edits the incoming commit makes identically out of the way.

    `upstream` must be a commit id, the same one the caller then merges:
    resolving `@{upstream}` again later could see a newer fetch than the
    one compared here. Only unstaged edits to paths the remote changed are
    considered. An untracked file the remote adds with the same content is
    removed, a tracked file the remote also deletes is restored so the merge
    deletes it, and a modification the remote makes identically is restored
    so the merge writes it. If any local edit to such a path differs, or a
    file changes between the check and the move, nothing is touched.
    """
    done = StepAside(root)
    steps = _plan(root, upstream)
    if not steps:
        return done
    for step in steps:
        target = root / step.path
        try:
            if step.action != "restore-deleted" and target.read_bytes() != step.content:
                done.undo()  # 比對之後又被改過
                return done
            if step.action == "remove":
                target.unlink()
            elif not _ok(_git(root, "checkout", "--", step.path)):
                done.undo()
                return done
        except OSError:
            done.undo()
            return done
        done.steps.append(step)
    return done


def printable(path: str) -> str:
    """A path safe to print: control characters from a remote name are escaped."""
    return "".join(char if char.isprintable() else repr(char)[1:-1] for char in path)
