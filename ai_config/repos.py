"""Keep chosen git repositories in step across machines, once a night.

Each machine picks its own list: a knowledge base wanted on a laptop may
not be wanted on a build server, so the list lives in this machine's
state directory and never in the data repository.

The order is commit, then rebase onto the remote, then push. Rebasing
with --autostash first would leave conflict markers in the working tree
when the stash comes back, with nothing left to abort; committing first
makes a conflict a plain `rebase --abort` that leaves the repository
exactly as it was, plus one local commit for a person to reconcile.

Nothing here resolves a conflict, forces a push or commits a file that
looks like a credential: those stop the repository and leave a note that
the next session start reads out.
"""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .subproc import UTF8

_GIT_TIMEOUT = 120
_PUSH_ATTEMPTS = 2


def _state_dir() -> Path:
    from .autoupdate import state_dir

    return state_dir()


def list_path() -> Path:
    return _state_dir() / "repos.json"


def result_path() -> Path:
    return _state_dir() / "repos-last.json"


def _git(repo: Path, *args: str, timeout: float = _GIT_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, **UTF8, check=False, timeout=timeout,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _out(result: subprocess.CompletedProcess) -> str:
    return result.stdout.strip()


def _error(result: subprocess.CompletedProcess) -> str:
    lines = (result.stderr.strip() or result.stdout.strip()).splitlines()
    return lines[-1] if lines else f"git 結束代碼 {result.returncode}"


# ─── the list ─────────────────────────────────────────────────

def registered() -> "list[Path]":
    try:
        raw = json.loads(list_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [Path(item) for item in raw if isinstance(item, str) and item]


def _save_list(repos: "list[Path]") -> None:
    path = list_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([str(repo) for repo in repos], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _key(path: Path) -> str:
    return os.path.normcase(str(path))


def _protected() -> "list[Path]":
    """Repositories acg already pushes through its own review."""
    from .paths import HOME, SCRIPT_DIR

    roots = [Path(SCRIPT_DIR).resolve()]
    memory = HOME / ".claude" / "shared-memory"
    if memory.exists():
        roots.append(memory.resolve())
    return roots


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _toplevel(path: Path) -> Path:
    if not path.is_dir():
        raise ValueError(f"{path} 不是目錄")
    found = _git(path, "rev-parse", "--show-toplevel")
    if found.returncode != 0:
        raise ValueError(f"{path} 不是 git repo")
    return Path(_out(found)).resolve()


def add(path: Path) -> "list[str]":
    """Register a repository and make sure the nightly run will reach it."""
    from . import nightly

    repo = _toplevel(path.expanduser().resolve())
    for root in _protected():
        if _overlaps(repo, root):
            raise ValueError(
                f"{repo} 是 acg 自己的資料庫或共用記憶,已經由 acg push 帶審查地同步,不能再加"
            )
    if _upstream(repo) is None:
        raise ValueError(
            f"{repo} 目前的分支沒有追蹤遠端;先 git push -u <remote> <branch> 再加"
        )
    repos = registered()
    lines = []
    if any(_key(existing) == _key(repo) for existing in repos):
        lines.append(f"{repo} 已經在清單裡")
    else:
        repos.append(repo)
        _save_list(repos)
        lines.append(f"已加入 {repo}")
    if not nightly.enabled("repos"):
        lines.extend(nightly.turn_on("repos"))
    return lines


def remove(path: Path) -> "list[str]":
    from . import nightly

    target = path.expanduser()
    target = target.resolve() if target.exists() else target.absolute()
    repos = registered()
    kept = [repo for repo in repos if _key(repo) != _key(target)]
    if len(kept) == len(repos):
        raise ValueError(f"{target} 不在清單裡")
    _save_list(kept)
    _forget_result(target)
    lines = [f"已移除 {target}(repo 本身沒動)"]
    if not kept and nightly.enabled("repos"):
        lines.extend(nightly.turn_off("repos"))
    return lines


# ─── one repository ───────────────────────────────────────────

def _upstream(repo: Path) -> "tuple[str, str, str, str] | None":
    """(branch, fetch remote, branch ref there, push remote) of the current branch."""
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch.returncode != 0:
        return None
    name = _out(branch)
    remote = _git(repo, "config", "--get", f"branch.{name}.remote")
    merge = _git(repo, "config", "--get", f"branch.{name}.merge")
    if remote.returncode != 0 or merge.returncode != 0:
        return None
    if _out(remote) == ".":
        return None
    push = _git(repo, "config", "--get", f"branch.{name}.pushRemote")
    if push.returncode != 0:
        push = _git(repo, "config", "--get", "remote.pushDefault")
    return name, _out(remote), _out(merge), _out(push) if push.returncode == 0 else _out(remote)


def _git_dir(repo: Path) -> "Path | None":
    found = _git(repo, "rev-parse", "--absolute-git-dir")
    return Path(_out(found)) if found.returncode == 0 else None


def _busy(repo: Path) -> str:
    """Why a person is in the middle of something here, or ''."""
    git_dir = _git_dir(repo)
    if git_dir is None:
        return "讀不到 .git"
    for name, why in (
        ("rebase-merge", "正在 rebase"), ("rebase-apply", "正在 rebase 或 am"),
        ("MERGE_HEAD", "正在 merge"), ("CHERRY_PICK_HEAD", "正在 cherry-pick"),
        ("REVERT_HEAD", "正在 revert"), ("index.lock", "有 index.lock(另一個 git 在跑,或上次中斷)"),
    ):
        if (git_dir / name).exists():
            return why
    # stash pop、merge --squash 衝突不留 MERGE_HEAD,只剩未合併的 index 項目;
    # 沒擋下來的話 add -A 會把衝突標記提交並推上去
    unmerged = _git(repo, "ls-files", "--unmerged")
    if unmerged.returncode != 0 or _out(unmerged):
        return "有未解決的衝突"
    return ""


def _blob_secret(repo: Path, path: str) -> bool:
    """A staged file git calls binary: search its bytes, and UTF-16 as text.

    PowerShell writes UTF-16 by default, and git shows such a file as
    binary with no added lines at all.
    """
    from .safety import SECRET_PATTERN

    blob = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", f":{path}"],
        capture_output=True, check=False, timeout=_GIT_TIMEOUT, stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if blob.returncode != 0:
        return True
    data = blob.stdout
    if SECRET_PATTERN.search(data):
        return True
    for encoding in ("utf-16-le", "utf-16-be"):
        text = data.decode(encoding, errors="ignore").encode("utf-8", "replace")
        if SECRET_PATTERN.search(text):
            return True
    return False


def _secret_paths(repo: Path) -> "list[str] | None":
    """Staged files whose added content looks like a credential."""
    from .safety import looks_like_secret

    # 中文檔名預設會被跳脫成八進位,對不上路徑;不認改名,改名的檔整份當新增來查
    base = ("-c", "core.quotepath=false", "diff", "--cached", "--no-renames", "--no-ext-diff")
    numstat = _git(repo, *base, "--numstat", "-z", "--diff-filter=AM")
    diff = _git(repo, *base, "--no-color", "-U0")
    if numstat.returncode != 0 or diff.returncode != 0:
        return None
    hits: list[str] = []
    for entry in numstat.stdout.split("\0"):
        added, _, rest = entry.partition("\t")
        _, _, path = rest.partition("\t")
        if added == "-" and path and _blob_secret(repo, path):
            hits.append(path)
    current, in_hunk = "", False
    for line in diff.stdout.splitlines():
        # 「+++ 」只在 hunk 之前才是檔頭;hunk 裡新增一行「++ x」也會長成這樣
        if line.startswith("diff --git "):
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        elif not in_hunk and line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else line[4:]
        elif (in_hunk and line.startswith("+") and current not in hits
              and looks_like_secret(line[1:])):
            hits.append(current)
    return hits


def _new_gitlinks(repo: Path, before: str) -> "list[str] | None":
    """Untracked clones inside the repo that add -A turned into broken submodules.

    Compared with the index as it was before add -A, so a submodule the
    user staged with `git submodule add` goes through.
    """
    raw = _git(repo, "-c", "core.quotepath=false", "diff-index", "--cached", "--no-renames",
               "--raw", "-z", "--diff-filter=A", before)
    if raw.returncode != 0:
        return None
    fields = raw.stdout.split("\0")
    return [
        fields[i + 1] for i in range(0, len(fields) - 1, 2)
        if fields[i].startswith(":000000 160000 ")
    ]


def _commit_local(repo: Path) -> "tuple[bool, str]":
    """Commit whatever is uncommitted. (ok, what happened).

    Every way out that does not commit puts the index back exactly as it
    was: a version staged and then edited further exists only there.
    """
    status = _git(repo, "status", "--porcelain")
    if status.returncode != 0:
        return False, _error(status)
    if not _out(status):
        return True, ""
    saved = _git(repo, "write-tree")
    head = _git(repo, "rev-parse", "--verify", "HEAD")
    if saved.returncode != 0 or head.returncode != 0:
        return False, f"讀不到目前的暫存區:{_error(saved if saved.returncode else head)}"
    committed = False
    try:
        staged = _git(repo, "add", "-A")
        if staged.returncode != 0:
            return False, f"git add 失敗:{_error(staged)}"
        if _git(repo, "diff", "--cached", "--quiet").returncode == 0:
            # 只有子模組裡面有改動:這層沒有東西可提交,照常同步遠端
            return True, ""
        nested = _new_gitlinks(repo, _out(saved))
        if nested is None or nested:
            return False, (
                "讀不到要提交的差異" if nested is None else
                f"裡面有另一個 git repo,會被提交成壞掉的子模組:{'、'.join(nested[:3])}"
                "。加進 .gitignore 或搬出去"
            )
        secrets = _secret_paths(repo)
        if secrets is None or secrets:
            return False, (
                "讀不到要提交的差異" if secrets is None else
                f"疑似含憑證,整個 repo 這次不同步:{'、'.join(secrets[:3])}"
                "。確認不是金鑰就自己 commit,是就移走或加進 .gitignore"
            )
        from .schedule_table import host_name

        # 不帶 --no-verify:使用者自己的 pre-commit(例如 gitleaks)也該擋得住
        done = _git(repo, "commit", "-q", "-m", f"chore: acg repos sync ({host_name()})")
        if done.returncode != 0:
            return False, f"commit 失敗:{_error(done)}"
        committed = True
        return True, "提交本機變更"
    finally:
        # commit 寫進去之後才逾時(例如 post-commit hook 很慢)時 HEAD 已經動了,
        # 這時退回舊的暫存區,下一次 commit 就會把剛提交的內容反向改回去
        moved = _git(repo, "rev-parse", "--verify", "HEAD")
        if not committed and moved.returncode == 0 and _out(moved) == _out(head):
            _git(repo, "read-tree", _out(saved))


def _abort_rebase(repo: Path) -> bool:
    git_dir = _git_dir(repo)
    if git_dir is None or not any((git_dir / n).exists() for n in ("rebase-merge", "rebase-apply")):
        return True
    return _git(repo, "rebase", "--abort").returncode == 0


def _catch_up(repo: Path, remote: str, merge: str) -> "tuple[str | None, str]":
    """Fetch, then replay local commits on the remote. (remote tip, note).

    The tip is None when this repo has to stop; the note says why.
    """
    fetched = _git(repo, "fetch", "--quiet", remote, merge)
    if fetched.returncode != 0:
        return None, f"fetch 失敗:{_error(fetched)}"
    # 記下 sha:之後有人另跑 git fetch 會把 FETCH_HEAD 換成別的分支
    tip = _git(repo, "rev-parse", "--verify", "FETCH_HEAD^{commit}")
    if tip.returncode != 0:
        return None, _error(tip)
    target = _out(tip)
    behind = _git(repo, "rev-list", "--count", f"HEAD..{target}")
    if behind.returncode != 0:
        return None, _error(behind)
    if _out(behind) == "0":
        return target, ""
    try:
        rebased = _git(repo, "rebase", "--quiet", "--no-autostash", "--rebase-merges", target)
    except subprocess.TimeoutExpired:
        restored = _abort_rebase(repo)
        return None, (
            f"rebase 超過 {_GIT_TIMEOUT} 秒," + ("已還原到同步前" if restored else "還原失敗,請手動 git rebase --abort")
        )
    if rebased.returncode == 0:
        return target, f"拉下 {_out(behind)} 個遠端 commit"
    conflicted = bool(_out(_git(repo, "ls-files", "--unmerged")))
    if not _abort_rebase(repo):
        return None, f"rebase 失敗且無法還原,請手動 git rebase --abort:{_error(rebased)}"
    if conflicted:
        return None, "跟遠端衝突,已還原到同步前(本機變更已 commit,沒有遺失);請手動 pull 解衝突"
    return None, f"rebase 失敗,已還原到同步前:{_error(rebased)}"


def sync_one(repo: Path) -> dict:
    """Bring one repository level with its remote. Returns its record."""
    notes: list[str] = []

    def record(ok: bool, note: str) -> dict:
        return {"when": datetime.now(UTC).isoformat(), "ok": ok,
                "note": "；".join([*notes, note] if note else notes) or "已是最新"}

    try:
        if not repo.is_dir():
            return record(False, "目錄不存在")
        if _toplevel(repo) != repo.resolve():
            return record(False, "已經不是 git repo 的根目錄")
        if any(_overlaps(repo.resolve(), root) for root in _protected()):
            return record(False, "現在跟 acg 的資料庫或共用記憶重疊,不同步;用 acg repos remove 移掉")
        busy = _busy(repo)
        if busy:
            return record(False, f"{busy},這次不動")
        upstream = _upstream(repo)
        if upstream is None:
            return record(False, "目前不在有追蹤遠端的分支上(detached HEAD 或沒設 upstream)")
        branch, remote, merge, push_remote = upstream
        # fork 流程每晚把推到 fork 的 commit 重放到 origin 上,第二晚起 push 必被拒;
        # 分支名跟追蹤的不同時,git push 的預設也不會這樣推。兩種都交給人
        if push_remote != remote:
            return record(False, f"push 目的地({push_remote})跟追蹤的遠端({remote})不同,不自動同步")
        if merge != f"refs/heads/{branch}":
            return record(False, f"分支 {branch} 追蹤的是不同名的 {merge},不自動同步")
        ok, note = _commit_local(repo)
        if not ok:
            return record(False, note)
        if note:
            notes.append(note)
        for attempt in range(_PUSH_ATTEMPTS):
            target, note = _catch_up(repo, remote, merge)
            if target is None:
                return record(False, note)
            if note:
                notes.append(note)
            ahead = _git(repo, "rev-list", "--count", f"{target}..HEAD")
            if ahead.returncode != 0:
                return record(False, _error(ahead))
            if _out(ahead) == "0":
                return record(True, "")
            pushed = _git(repo, "push", "--quiet", remote, f"HEAD:{merge}")
            if pushed.returncode == 0:
                return record(True, f"推上 {_out(ahead)} 個 commit")
            # 另一台剛好在這之間推了:再抓一次重放;還是被拒就留給人看
            if attempt + 1 == _PUSH_ATTEMPTS:
                return record(False, f"push 失敗:{_error(pushed)}")
    except subprocess.TimeoutExpired:
        return record(False, f"git 超過 {_GIT_TIMEOUT} 秒沒回應")
    except (OSError, ValueError) as exc:
        return record(False, str(exc))
    return record(False, "未預期的狀態")


# ─── results ──────────────────────────────────────────────────

def _load_results() -> dict:
    try:
        raw = json.loads(result_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}


def _save_results(results: dict) -> None:
    path = result_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


def _forget_result(repo: Path) -> None:
    results = _load_results()
    kept = {k: v for k, v in results.items() if _key(Path(k)) != _key(repo)}
    if kept != results:
        _save_results(kept)


def results() -> "list[tuple[Path, dict]]":
    """The last outcome of every registered repository, in list order."""
    stored = {_key(Path(k)): v for k, v in _load_results().items()}
    return [(repo, stored[_key(repo)]) for repo in registered() if _key(repo) in stored]


def failures() -> "list[tuple[Path, dict]]":
    return [(repo, result) for repo, result in results() if result.get("ok") is not True]


def sync(only: "Path | None" = None) -> "tuple[int, list[tuple[Path, dict]]]":
    """Sync every registered repository, or just one, recording each outcome.

    A run over one repository leaves the others' records alone, so a manual
    retry of one cannot hide another's conflict.
    """
    from .locking import exclusive_lock

    repos = registered()
    if only is not None:
        target = only.expanduser()
        target = target.resolve() if target.exists() else target.absolute()
        repos = [repo for repo in repos if _key(repo) == _key(target)]
        if not repos:
            raise ValueError(f"{target} 不在清單裡;先 acg repos add {target}")
    with exclusive_lock(".acg-repos.lock") as acquired:
        if not acquired:
            raise RuntimeError("已經有一次 repo 同步正在進行,這次不做")
        stored = _load_results()
        done = []
        for repo in repos:
            outcome = sync_one(repo)
            stored = {k: v for k, v in stored.items() if _key(Path(k)) != _key(repo)}
            stored[str(repo)] = outcome
            _save_results(stored)
            done.append((repo, outcome))
    return (0 if all(r["ok"] for _, r in done) else 1), done
