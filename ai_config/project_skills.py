"""A project's own skills, written once in .agents/skills and copied for Claude.

Codex and Antigravity read .agents/skills; Claude reads only .claude/skills.
See docs/project-skill-sync-spec.md.
"""

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import deploy_record, paths
from .console import log_error, log_info, log_success, log_warn
from .paths import EXCLUDED_FILES
from .safety import assert_no_symlinks, is_reparse_point

SOURCE = ".agents/skills"
TARGET = ".claude/skills"
RECORD = ".acg-skill-sync.json"
_UNSAFE_NAME_CHARS = frozenset('<>:"/\\|?*')
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"{port}{number}" for port in ("COM", "LPT") for number in range(1, 10)}
)
_NEVER_COPIED = EXCLUDED_FILES | {".git"}

CREATE = "create"
UPDATE = "update"
REMOVE = "remove"
READY = "ready"
ADOPT = "adopt"
FORGET = "forget"
CONFLICT = "conflict"
_WRITES = frozenset({CREATE, UPDATE, REMOVE, ADOPT, FORGET})
_REASONS = {
    CREATE: "新增給 Claude",
    UPDATE: "來源改了,更新副本",
    REMOVE: "來源已刪,移除副本",
    READY: "已一致",
    ADOPT: "內容相同,更新紀錄",
    FORGET: "來源與副本都不在了,清掉紀錄",
}


@dataclass(frozen=True)
class Step:
    name: str
    action: str
    reason: str


def _valid_name(name: object) -> bool:
    return (
        isinstance(name, str) and bool(name) and name == name.strip()
        and not name.startswith(".") and not name.endswith(".")
        and not set(name) & _UNSAFE_NAME_CHARS
        and name.split(".")[0].upper() not in _RESERVED_NAMES
    )


def _refuse_outside_a_project(project: Path) -> None:
    """Home holds Codex's global ~/.agents/skills, and the database has its own rules."""
    home = Path(paths.HOME).resolve()
    if project == home or home.is_relative_to(project):
        raise RuntimeError(f"{project} is the home directory, not a project")
    if project.is_relative_to(Path(paths.SCRIPT_DIR).resolve()):
        raise RuntimeError(f"{project} is the data repository, not a project")


def _refuse_links(project: Path) -> None:
    for relative in (".agents", SOURCE, ".claude", TARGET, RECORD):
        path = project / relative
        if is_reparse_point(path):
            raise RuntimeError(f"Refusing reparse point: {relative}")


def load_record(project: Path) -> dict[str, str]:
    path = project / RECORD
    if not path.exists():
        return {}
    # 名稱會拿去組路徑與刪除;這個檔案隨專案提交,不能信任
    try:
        skills = json.loads(path.read_text(encoding="utf-8"))["skills"]
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"{RECORD} is not valid: {error!r}") from error
    if not isinstance(skills, dict) or not all(
        _valid_name(name) and isinstance(print_, str) for name, print_ in skills.items()
    ):
        raise RuntimeError(f"{RECORD} is not valid")
    return skills


def _save_record(project: Path, record: dict[str, str]) -> None:
    path = project / RECORD
    if not record:
        path.unlink(missing_ok=True)
        return
    text = json.dumps(
        {"skills": record}, ensure_ascii=False, indent=2, sort_keys=True,
    ) + "\n"
    descriptor, name = tempfile.mkstemp(prefix=f"{RECORD}.", dir=project)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        # mkstemp 的檔案只有擁有者能讀;共用目錄的其他帳號會整個讀不到
        Path(name).chmod(0o644)
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def _files(root: Path, *, for_copy: bool) -> dict[str, bytes]:
    """Every file under root by portable path; for_copy drops what is never copied."""
    assert_no_symlinks(root)
    found: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_dir():
            continue
        if not path.is_file():
            raise RuntimeError(f"Unexpected file type: {path}")
        if for_copy and _NEVER_COPIED.intersection(relative.parts):
            continue
        found[relative.as_posix()] = path.read_bytes()
    return found


def fingerprint(files: dict[str, bytes]) -> str:
    # autocrlf 的 Windows 取出同一份提交只有換行不同,那不算有人改過
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(files[name].replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def _modes_differ(src: Path, dst: Path, names: dict[str, bytes]) -> bool:
    """Whether an executable bit has not reached the copy; a skill's script must still run."""
    if os.name == "nt":
        return False
    return any(
        bool((src / name).stat().st_mode & 0o111) != bool((dst / name).stat().st_mode & 0o111)
        for name in names
    )


def _plain_dir(path: Path) -> bool:
    return path.is_dir() and not is_reparse_point(path)


def _sources(project: Path) -> tuple[dict[str, Path], set[str]]:
    """Skill directories, and directories that hold no SKILL.md."""
    root = project / SOURCE
    skills: dict[str, Path] = {}
    incomplete: set[str] = set()
    if not root.is_dir():
        return skills, incomplete
    placed = deploy_record.load(project).files
    for child in sorted(root.iterdir()):
        if child.name.startswith("."):
            continue
        if is_reparse_point(child):
            raise RuntimeError(f"Refusing reparse point: {SOURCE}/{child.name}")
        if not child.is_dir():
            incomplete.add(child.name)
            continue
        if not _valid_name(child.name):
            raise RuntimeError(f"Unsafe skill name: {child.name}")
        # deploy 放的是資料庫裡的個人技能,不是這個專案的;照抄就會進專案的提交
        if any(path.startswith(f"{SOURCE}/{child.name}/") for path in placed):
            incomplete.add(child.name)
        elif (child / "SKILL.md").is_file():
            skills[child.name] = child
        else:
            incomplete.add(child.name)
    return skills, incomplete


def _plan_source(name: str, src: Path, dst: Path, recorded: "str | None") -> Step:
    if not dst.exists() and not is_reparse_point(dst):
        return Step(name, CREATE, _REASONS[CREATE])
    if not _plain_dir(dst):
        return Step(name, CONFLICT, "專案裡已有同名、但不是一般目錄的東西,不覆蓋")
    wanted = _files(src, for_copy=True)
    current = _files(dst, for_copy=False)
    if wanted == current and not _modes_differ(src, dst, wanted):
        # 合併或兩邊一起手改後指紋會過期;現在不補,下次改來源就成了假衝突
        action = READY if recorded == fingerprint(current) else ADOPT
        return Step(name, action, _REASONS[action])
    if recorded is not None and fingerprint(current) == recorded:
        return Step(name, UPDATE, _REASONS[UPDATE])
    reason = ("副本同步後被改過,不覆蓋" if recorded is not None
              else "已有不是 acg 放的同名技能,不覆蓋")
    return Step(name, CONFLICT, reason)


def _plan_orphan(name: str, dst: Path, recorded: str) -> Step:
    if not dst.exists() and not is_reparse_point(dst):
        return Step(name, FORGET, _REASONS[FORGET])
    if _plain_dir(dst) and fingerprint(_files(dst, for_copy=False)) == recorded:
        return Step(name, REMOVE, _REASONS[REMOVE])
    return Step(name, CONFLICT, "來源已刪,但副本被改過,不刪除")


def plan(project: Path) -> list[Step]:
    """What a sync would do, without writing anything."""
    _refuse_outside_a_project(project)
    _refuse_links(project)
    record = load_record(project)
    if record and not (project / SOURCE).is_dir():
        # sparse checkout 或還沒初始化的 submodule 會讓來源整個不見,不能當成全部刪掉了
        raise RuntimeError(
            f"{SOURCE} is missing but {RECORD} lists skills; "
            f"to drop them, delete their copies and {RECORD}",
        )
    skills, incomplete = _sources(project)
    target = project / TARGET
    steps = [
        _plan_source(name, src, target / name, record.get(name))
        for name, src in skills.items()
    ]
    for name in sorted(incomplete & record.keys()):
        # 名稱還在就不是刪掉了;先讓人看到
        steps.append(Step(name, CONFLICT, "來源還在但不是可同步的技能,不動副本"))
    for name in sorted(record.keys() - skills.keys() - incomplete):
        steps.append(_plan_orphan(name, target / name, record[name]))
    return steps


def _unchanged(dst: Path, expected: "str | None") -> None:
    current = fingerprint(_files(dst, for_copy=False)) if _plain_dir(dst) else None
    if (current if dst.exists() or is_reparse_point(dst) else None) != expected:
        raise RuntimeError(f"Copy changed during sync: {dst.name}")


def _install(src: Path, target: Path, name: str, expected: "str | None") -> str:
    """Build the copy beside its place, then swap, so a failure leaves the old one."""
    target.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".acg-sync-{name}-", dir=target))
    retired = target / f".acg-retired-{staging.name}"
    dst = target / name
    try:
        files = _files(src, for_copy=True)
        for relative in files:
            copied = staging / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / relative, copied)
        # mkdtemp 的目錄只有擁有者能讀,直接換上去別人就讀不到了
        shutil.copystat(src, staging)
        # 放在換上去的前一刻:規劃之後才被改或才出現的副本都不能帶走
        _unchanged(dst, expected)
        existed = dst.exists()
        if existed:
            os.replace(dst, retired)
        try:
            os.replace(staging, dst)
        except OSError:
            if existed:
                os.replace(retired, dst)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(retired, ignore_errors=True)
    return fingerprint(files)


def _carry_out(project: Path, step: Step, record: dict[str, str]) -> None:
    src = project / SOURCE / step.name
    target = project / TARGET
    dst = target / step.name
    if step.action in (CREATE, UPDATE):
        expected = record[step.name] if step.action == UPDATE else None
        record[step.name] = _install(src, target, step.name, expected)
    elif step.action == ADOPT:
        record[step.name] = fingerprint(_files(dst, for_copy=False))
    elif step.action == REMOVE:
        _unchanged(dst, record[step.name])
        shutil.rmtree(dst)
        del record[step.name]
    elif step.action == FORGET:
        del record[step.name]


def sync(project: Path) -> list[Step]:
    """Carry out the plan; a conflict leaves that skill alone and the rest proceed."""
    steps = plan(project)
    record = load_record(project)
    try:
        for step in steps:
            if step.action in _WRITES:
                _carry_out(project, step, record)
    finally:
        # 中途失敗也要記下已經放好的,否則下一次會把它們當成別人的
        if any(step.action in _WRITES for step in steps):
            _save_record(project, record)
    return steps


def preview(project: Path) -> list[dict]:
    """The desktop app's change list, in the shape its preview panel takes."""
    operations = {CREATE: "create", UPDATE: "modify", REMOVE: "delete",
                  ADOPT: "modify", FORGET: "modify"}
    return [
        {"category": "deploy", "tool": "Claude",
         "operation": operations.get(step.action, "skip"),
         "source": str(project / SOURCE / step.name),
         "destination": str(project / TARGET / step.name),
         "physical_target": None, "shared": False, "reason": step.reason}
        for step in plan(project)
    ]


def execute(project: Path) -> int:
    return 1 if _report(sync(project), project) else 0


def _report(steps: list[Step], project: Path) -> list[Step]:
    """Print each step; returns the conflicts."""
    conflicts = [step for step in steps if step.action == CONFLICT]
    for step in steps:
        line = f"{step.name}:{step.reason}"
        if step.action == CONFLICT:
            log_error(line)
        elif step.action == READY:
            log_info(line)
        else:
            log_success(line)
    if not steps:
        log_info(f"{project} 的 {SOURCE} 沒有技能")
    return conflicts


def run_skill_sync(args: list[str], usage: str) -> int:
    rest = [token for token in args if token != "--check"]
    check = len(rest) != len(args)
    if len(args) - len(rest) > 1 or len(rest) > 1 or (rest and rest[0].startswith("-")):
        log_error(usage)
        return 1
    project = Path(rest[0] if rest else ".").resolve()
    if not project.is_dir():
        log_error(f"Project directory not found: {project}")
        return 1
    try:
        if not check:
            return 1 if _report(sync(project), project) else 0
        steps = plan(project)
        drifted = [step for step in steps if step.action != READY]
        for step in drifted:
            log_warn(f"{step.name}:{step.reason}")
        if drifted:
            log_error(f"{len(drifted)} 個技能跟 {SOURCE} 不一致;執行 skill sync")
            return 1
        log_success(f"{TARGET} 與 {SOURCE} 一致")
        return 0
    except (OSError, RuntimeError, UnicodeError) as error:
        log_error(str(error))
        return 1
