"""acg memory: the shared notebook's status, enable, disable, path and push."""

import json
import shlex
import shutil
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

from .. import memory, memory_hooks
from ..console import log_error, log_header, log_info, log_success, log_warn
from ..locking import apply_lock
from ..paths import BACKUP_BASE, ENTRYPOINT, MEMORY_LINK, tilde

USAGE = (
    f"Usage: {ENTRYPOINT} memory "
    "<status|enable [codex|agy]|disable [codex|agy]|"
    "adopt [路徑|all|--scan [目錄]]|release [路徑]|path|push|"
    "handoff|autopush>"
)


def run_memory(args: list[str]) -> int:
    try:
        return _run_memory(args)
    except (OSError, RuntimeError, ValueError) as exc:
        log_error(str(exc))
        return 1


def _run_memory(args: list[str]) -> int:
    command = args[0] if args else "status"
    rest = args[1:]
    if command == "status" and not rest:
        return _status()
    if command in {"enable", "disable"} and rest in (["codex"], ["agy"]):
        return _host(command, rest[0])
    # 別的指令都吃 all,所以這裡也該吃;--scan 留著不動
    if command == "adopt" and rest and rest[0] == "all":
        return _adopt_scan(rest[1:])
    if command in {"adopt", "release"} and rest and rest[0] != "--scan":
        if len(rest) > 1:
            log_error(f"Usage: {ENTRYPOINT} memory {command} [專案路徑|all|--scan [目錄]]")
            return 1
        chosen = Path(rest[0]).expanduser()
        if not chosen.is_dir():
            log_error(f"找不到這個專案目錄:{chosen}")
            return 1
        return execute(command, memory.project_root(chosen)).code
    if command == "adopt" and rest and rest[0] == "--scan":
        return _adopt_scan(rest[1:])
    if command in {"enable", "disable", "adopt", "release"} and not rest:
        project = memory.project_root() if command in {"adopt", "release"} else None
        return execute(command, project).code
    if command == "path" and set(rest) <= {"--global", "--project"}:
        return _path(rest)
    if command == "autopush":
        return _autopush(rest)
    if command == "push":
        from .. import autopush as auto
        from .push import MEMORY_SCOPE, do_push

        stale = None
        if len(rest) == 2 and rest[0] == "--if-stale":
            try:
                stale = float(rest[1])
            except ValueError:
                log_error("--if-stale 要接小時數,例如 --if-stale 12")
                return 1
            rest = []
        if stale is not None:
            decision = auto.decide(stale)
            # 時間表住在記憶目錄裡,decide 會先把落後的部分接上,所以讓位要在
            # 它之後才看得到別台剛認領的時段。反過來的話,撞號要等到隔天才發現
            moved = auto.reconcile_slot()
            if moved:
                log_info(moved)
            if not decision.push:
                log_info(f"跳過自動推送:{decision.reason}")
                return 0
        allow_secrets = rest == ["--allow-secrets"]
        if rest and not allow_secrets:
            log_error(
                f"Usage: {ENTRYPOINT} memory push "
                "[--allow-secrets] [--if-stale <小時>]"
            )
            return 1
        if stale is not None:
            # 排程沒有終端機,確認提示讀到 EOF 就會當成拒絕。只跳過確認,
            # 憑證檢查照跑:allow_secrets 維持 False
            from ..console import set_force

            set_force(True)
            code = do_push(MEMORY_SCOPE, allow_secrets=False, scheduled=True)
            if code == 0:
                auto.record_push()
            return code
        return do_push(MEMORY_SCOPE, allow_secrets=allow_secrets)
    if command == "handoff":
        return _handoff(rest)
    log_error(USAGE)
    return 1


_AUTOPUSH_USAGE = (
    f"Usage: {ENTRYPOINT} memory autopush [status | enable [時] | disable]"
)


def _autopush(rest: list[str]) -> int:
    from .. import autopush as auto

    action = rest[0] if rest else "status"
    args = rest[1:]
    try:
        if action == "status" and not args:
            state = auto.status()
            log_info(f"平台:{state['platform']}")
            if state["installed"]:
                log_success("已排定每天自動推送記憶")
            else:
                log_info(
                    f"尚未排定;沒有排程時,{ENTRYPOINT} 每次執行也會順手檢查"
                )
            log_info(f"上次推送:{state['last_push'] or '沒有紀錄'}")
            log_info(f"現在執行的話:{state['reason']}")
            return 0
        if action == "enable" and len(args) <= 1:
            # None 表示「照時間表分配」;給了數字才是手動指定
            hour = int(args[0]) if args else None
            for line in auto.enable(hour):
                log_success(line)
            return 0
        if action == "disable" and not args:
            for line in auto.disable():
                log_info(line)
            return 0
    except (OSError, RuntimeError, ValueError) as exc:
        log_error(str(exc))
        return 1
    log_error(_AUTOPUSH_USAGE)
    return 1


_HANDOFF_USAGE = (
    f"Usage: {ENTRYPOINT} memory handoff "
    "[list [專案路徑] | write <線> <內容> | claim <線> | done <線> | "
    "remind [status|enable [百分比]|disable]]"
)


def _handoff(rest: list[str]) -> int:
    from .. import handoff as hand

    action = rest[0] if rest else "list"
    args = rest[1:]
    try:
        if action == "remind":
            return _handoff_remind(args)
        if action == "list" and len(args) <= 1:
            return _handoff_list(Path(args[0]) if args else None)
        if action == "write" and len(args) >= 2:
            note = hand.write(args[0], " ".join(args[1:]))
            log_success(f"已記下交接:{note.thread}")
            # 名稱含空格時要引號,否則照著貼會被拆成多個參數
            log_info(
                f"下個 session 用 {ENTRYPOINT} memory handoff claim "
                f"{shlex.quote(note.name)}"
            )
            return 0
        if action == "claim" and len(args) == 1:
            note, displaced = hand.claim(args[0])
            log_success(f"已認領:{note.thread}")
            # 接走的是別人放著沒收的線,說出前一個持有者,接手的人才
            # 知道這份進度可能停在半路,不是寫完才交出來的
            if displaced:
                log_warn(
                    f"這條線原本由 {hand.short_id(displaced)} 持有,超過 "
                    f"{hand.STALE_AFTER_HOURS} 小時沒有動靜,已接手"
                )
            print()
            print(note.body)
            return 0
        if action == "done" and len(args) == 1:
            note = hand.done(args[0])
            log_success(f"已結束:{note.thread}")
            return 0
    except (OSError, RuntimeError, ValueError) as exc:
        log_error(str(exc))
        return 1
    log_error(_HANDOFF_USAGE)
    return 1


def _handoff_remind(args: list[str]) -> int:
    from .. import handoff_reminder as remind

    action = args[0] if args else "status"
    rest = args[1:]
    if action == "status" and not rest:
        state = remind.status()
    elif action == "enable" and len(rest) <= 1:
        threshold = int(rest[0]) if rest else remind.DEFAULT_THRESHOLD
        state = remind.configure(True, threshold)
    elif action == "disable" and not rest:
        state = remind.configure(False)
    else:
        log_error(_HANDOFF_USAGE)
        return 1
    if state["installed"]:
        log_success(f"Claude 交接提醒已啟用，門檻 {state['threshold']}%")
    elif state["enabled"]:
        log_warn("交接提醒安裝不完整，請重新執行 handoff remind enable")
    else:
        log_info("Claude 交接提醒未啟用")
    log_info("只提醒，不自動寫入交接；使用新會話確認 hooks 生效")
    return 0


def _handoff_list(cwd: "Path | None" = None) -> int:
    """List one project's threads; a path lets a session read another's."""
    from .. import handoff as hand

    if cwd is not None and not cwd.is_dir():
        raise ValueError(f"找不到這個專案目錄:{cwd}")
    # 每個 session 開工都會走這裡,所以歸檔掛在這:結束很久的線自己
    # 讓開,不必有人記得清。搬不動就算了,列表比歸檔重要
    try:
        archived = hand.archive_finished()
    except OSError:
        archived = []
    # 這些檔案有進版控,不說一聲的話下次 push 會冒出沒人解釋的改名
    if archived:
        log_info(
            f"已把 {len(archived)} 條結束超過 {hand.ARCHIVE_AFTER_DAYS} 天的線"
            f"移到 {hand.ARCHIVE_DIR_NAME}/:{'、'.join(archived)}"
        )
    # 結束的線留在磁碟上當紀錄,但這裡問的是「有什麼可以接手」,
    # 把它們一起列出來只會讓人多判斷一次哪條還活著
    notes = [
        note for note in hand.load_all(memory.project_key(cwd).key)
        if note.state != hand.DONE
    ]
    if not notes:
        where = f"{cwd} 這個專案" if cwd is not None else "這個專案"
        log_info(f"{where}沒有待接手的工作線")
        return 0
    for note in notes:
        mark = {hand.OPEN: "○", hand.CLAIMED: "◐"}.get(note.state, "○")
        held = f" ← {hand.short_id(note.claimed_by)}" if note.claimed_by else ""
        age = hand.age_in_days(note.created)
        # 一條線放了幾天,就是該不該接它的理由;當天開的不用說
        waited = f"  ({age} 天前開的)" if age else ""
        # 擱著沒動的線跟剛寫好的長得一樣,而裡面的進度可能早就被
        # 別處的工作蓋過去了。接手前該先讀一遍,不是照著做
        stale = "  ⚠ 可能已過期" if hand.is_stale(note) else ""
        print(f"  {mark} {note.name}{held}{waited}{stale}")
        print(f"    {hand.summary(note.body)}")
    return 0


def _status() -> int:
    log_header("Shared memory")
    state = memory.inspect()
    log_info(f"記憶目錄:{tilde(state.directory)}")
    if not state.index_exists:
        log_warn(f"尚未建立索引,執行 {ENTRYPOINT} memory enable")

    if state.link == "ok":
        log_success(f"連結 {tilde(MEMORY_LINK)} 指向記憶目錄")
    elif state.link == "missing":
        log_warn(f"連結 {tilde(MEMORY_LINK)} 尚未建立")
    else:
        log_error(f"連結 {tilde(MEMORY_LINK)} 被佔用:{state.link_detail}")

    _report_entry("Claude 規則(即時)", state.live_block)
    if state.source_exists:
        _report_entry("Claude 規則(資料庫)", state.source_block)
    else:
        log_warn("資料庫沒有 claude/CLAUDE.md,規則區塊無法同步到其他機器")
    _report_entry("agy 規則", state.agy_rules)
    _report_entry("Codex 規則", state.codex_block)
    if state.codex_override:
        log_warn(f"{tilde(memory.codex_override_path())} 存在,會遮蔽 Codex 的共用規則")

    if state.index_unlisted:
        log_warn(
            f"有 {len(state.index_unlisted)} 則筆記沒有寫進索引,"
            "下次開會話不會被看到:"
        )
        for name in state.index_unlisted:
            print(f"  {memory.TOPICS_NAME}/{name}")
    if state.index_dangling:
        log_warn(f"索引有 {len(state.index_dangling)} 個連結指向不存在的檔案:")
        for name in state.index_dangling:
            print(f"  {memory.TOPICS_NAME}/{name}")

    if state.secret_notes:
        log_error(f"有 {len(state.secret_notes)} 則筆記像是含有憑證,push 會擋下:")
        for name in state.secret_notes:
            print(f"  {name}")
        log_info("請先移除內容;本機日誌不同步,不在此列")

    if state.changes is None:
        log_info("記憶目錄不在 git 管理之下")
    elif state.changes:
        log_info(
            f"有 {len(state.changes)} 個尚未保存的記憶變更(執行 {ENTRYPOINT} memory push)"
        )
    else:
        log_success("記憶已全部保存")

    key = state.project
    scope = "跨機器穩定" if key.stable else "只在這台有效,專案沒有 git 遠端"
    log_info(f"目前專案鍵值:{key.key}({scope})")
    if state.project_exists:
        log_info(f"專案記憶:{tilde(state.project_dir)}")
    else:
        log_info("專案記憶尚未建立,AI 第一次「記住」專案內容時會自己建")

    _report_hosts()
    if not state.remember:
        return 0
    if state.journal_config == "ours":
        log_success("remember 日誌已改存到共用記憶")
    elif state.journal_config == "other":
        log_warn(f"remember 的 data_dir 已另外設定:{state.journal_config_value}")
    else:
        log_info(
            f"remember 日誌仍在各專案的 .remember(執行 {ENTRYPOINT} memory enable)"
        )
    _report_journal(state.journal, state.journal_detail)
    _report_unadopted()
    return 0


def _report_unadopted() -> None:
    """Say how many other projects are waiting, or nobody finds the scan."""
    try:
        pending = memory.unadopted_below(memory.HOME)
    except OSError:
        return
    here = memory.project_root()
    others = [project for project in pending if project != here]
    if others:
        log_info(
            f"另外 {len(others)} 個專案的日誌還沒同步"
            f"({ENTRYPOINT} memory adopt all 可一次處理)"
        )


def _report_journal(state: str, detail: str) -> None:
    if state == "adopted":
        log_success(f"這個專案的日誌跟著共用記憶同步:{tilde(Path(detail))}")
    elif state == "local":
        log_info(
            f"這個專案的日誌只在這台:{tilde(Path(detail))}({ENTRYPOINT} memory adopt 可同步)"
        )
    elif state == "legacy":
        log_info(
            f"這個專案的日誌還在 {tilde(Path(detail))}({ENTRYPOINT} memory adopt 可同步)"
        )
    elif state == "foreign":
        log_warn(f"這個專案的日誌連結指向別處:{detail}")


def _report_entry(label: str, installed: bool) -> None:
    if installed:
        log_success(f"{label}已安裝")
    else:
        log_warn(f"{label}未安裝")


def _backup(paths: list[Path]) -> Path | None:
    existing = list(dict.fromkeys(path for path in paths if path.is_file()))
    if not existing:
        return None
    memory.assert_plain_path(BACKUP_BASE, directory=True)
    folder = BACKUP_BASE / f"memory-{uuid.uuid4().hex}"
    folder.mkdir(parents=True)
    manifest = {}
    for number, path in enumerate(existing):
        memory.assert_plain_path(path, directory=False)
        relative = f"{number}/{path.name}"
        destination = folder / relative
        destination.parent.mkdir()
        shutil.copy2(path, destination)
        manifest[relative] = str(path)
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log_info(f"已備份到 {tilde(folder)}")
    return folder


@dataclass
class MemoryExecutionResult:
    code: int = 0
    backup_path: str | None = None
    recovery_required: bool = False


class MemoryOperationError(RuntimeError):
    def __init__(self, message: str, backup: Path | None, recovery: bool):
        super().__init__(message)
        self.backup_path = str(backup) if backup is not None else None
        self.recovery_required = recovery


def execute(
    action: str, project: Path | None = None, *, lock_held: bool = False,
) -> MemoryExecutionResult:
    """Execute a validated lifecycle action; callers never change process cwd."""
    from ..memory_plan import plan

    operation = plan(action, project)
    if action in {"adopt", "release"} and not operation.changes:
        # 沒有事要做就不要建備份;預覽已經證明什麼都不會改
        for warning in operation.warnings:
            log_warn(warning)
        if action == "adopt":
            log_success("這個專案的日誌已經在共用記憶裡")
        else:
            log_info("這個專案的日誌本來就沒有接進共用記憶")
        return MemoryExecutionResult(code=0)
    with _mutation(
        enabling=action == "enable", action=action, project=project,
        lock_held=lock_held,
    ) as result:
        if action == "enable":
            result.code = _enable()
        elif action == "disable":
            result.code = _disable()
        elif action == "adopt":
            result.code = _adopt(operation.project)
        else:
            result.code = _release(operation.project)
    return result


@contextmanager
def _mutation(
    *, enabling: bool, action: str | None = None,
    project: Path | None = None, lock_held: bool = False,
):
    # Preflight before creating the lock or snapshot: refused inputs stay intact.
    memory.preflight_memory()
    memory.preflight_rules(enabling=enabling)
    memory.assert_plain_path(BACKUP_BASE, directory=True)
    with nullcontext() if lock_held else apply_lock():
        if action is not None:
            from ..memory_plan import plan

            operation = plan(action, project)
        else:
            operation = None
        memory.preflight_memory()
        memory.preflight_rules(enabling=enabling)
        paths = [memory.rules_target(path) for path in memory.instruction_paths()]
        paths.extend([
            memory.agy_rules_path(),
            memory.REMEMBER_USER_CONFIG,
            memory.index_path(),
            memory.memory_dir() / ".gitignore",
        ])
        if action in {"enable", "disable"}:
            paths.append(memory_hooks.settings_path())
        originals = {
            path: path.read_bytes() if path.is_file() else None
            for path in paths
        }
        link_state, _ = memory.link_state()
        missing_directories = set()
        if operation is not None:
            for change in operation.changes:
                destination = Path(change["destination"])
                candidates = list(destination.parents)
                if change["operation"] == "mkdir":
                    candidates.append(destination)
                for directory in candidates:
                    if not directory.exists() and not memory.is_reparse_point(directory):
                        missing_directories.add(directory)
        backup_paths = list(paths)
        if action in {"adopt", "release"} and project is not None:
            root = memory.project_root(project)
            journal_directories = [memory.project_journal_dir(memory.project_key(root))]
            if action == "adopt":
                journal_directories.extend([root / ".remember", memory.journal_link(root)])
            for directory in journal_directories:
                if memory.is_reparse_point(directory):
                    continue
                memory._check_journal_tree(directory)
                if directory.exists():
                    backup_paths.extend(p for p in directory.rglob("*") if p.is_file())
        snapshot = _backup(backup_paths)
        if snapshot is not None:
            state = {"memory_link": link_state}
            if project is not None:
                root = memory.project_root(project)
                state["journal"] = {
                    "path": str(memory.journal_link(root)),
                    "state": memory.journal_state(root),
                }
            (snapshot / "links.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        result = MemoryExecutionResult(backup_path=str(snapshot) if snapshot else None)
        written: dict[Path, bytes | None] = {}
        observer = memory.WRITE_OBSERVER.set(lambda path, content: written.__setitem__(path, content))
        try:
            yield result
        except (OSError, RuntimeError, ValueError) as failure:
            errors = []
            for path, content in originals.items():
                if path not in written:
                    continue
                try:
                    memory.assert_plain_path(path, directory=False)
                    current = path.read_bytes() if path.exists() else None
                    if current == content:
                        continue
                    if current != written[path]:
                        raise RuntimeError("檔案已被外部修改，保留目前內容")
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(content)
                except (OSError, RuntimeError) as exc:
                    errors.append(f"無法還原 {tilde(path)}:{exc}")
            try:
                current_link = memory.link_state()[0]
                if current_link not in {"ok", "missing"}:
                    raise RuntimeError("共用連結已被外部修改")
                if link_state == "missing" and current_link == "ok":
                    memory.remove_link()
                elif link_state == "ok" and current_link == "missing":
                    ok, detail = memory.create_link()
                    if not ok:
                        raise RuntimeError(detail)
            except (OSError, RuntimeError) as exc:
                errors.append(f"無法還原共用連結:{exc}")
            for directory in sorted(missing_directories, key=lambda p: len(p.parts), reverse=True):
                try:
                    memory.assert_plain_path(directory, directory=True)
                    if directory.exists():
                        directory.rmdir()
                except (OSError, RuntimeError) as exc:
                    errors.append(f"保留操作建立的目錄 {directory}:{exc}")
            if snapshot:
                log_warn(f"操作失敗,原始檔案備份:{tilde(snapshot)}")
            for error in errors:
                log_error(error)
            recovery = bool(errors) or bool(getattr(failure, "recovery_required", False))
            message = str(failure)
            if errors:
                message += ";" + ";".join(errors)
            raise MemoryOperationError(message, snapshot, recovery) from failure
        finally:
            memory.WRITE_OBSERVER.reset(observer)


def _enable() -> int:
    log_header("Enable shared memory")
    if memory.ensure_index():
        log_success(f"建立索引 {tilde(memory.index_path())}")

    ok, detail = memory.create_link()
    if ok:
        log_success(f"連結 {tilde(MEMORY_LINK)} -> {tilde(memory.memory_dir())}")
    else:
        raise RuntimeError(f"無法建立連結 {tilde(MEMORY_LINK)}:{detail}")

    if memory.install_block(memory.live_rules_path()):
        log_success(f"規則區塊寫入 {tilde(memory.live_rules_path())}")
    else:
        log_info("Claude 即時規則已有區塊")
    if memory.source_rules_path().is_file():
        if memory.install_block(memory.source_rules_path()):
            log_success(f"規則區塊寫入 {tilde(memory.source_rules_path())}")
            log_info(f"執行 {ENTRYPOINT} push claude 讓其他機器也拿到規則")
    else:
        log_warn(
            f"資料庫沒有 claude/CLAUDE.md,先執行 {ENTRYPOINT} init claude 再重跑一次"
        )
    if memory.install_agy_rules():
        log_success(f"agy 規則寫入 {tilde(memory.agy_rules_path())}")
    for path in memory.instruction_paths():
        if (
            path not in (memory.live_rules_path(), memory.source_rules_path())
            and memory.install_block(path)
        ):
            log_success(f"規則區塊寫入 {tilde(path)}")
    if memory.ensure_journal_ignored():
        log_success("memory/.gitignore 排除本機的日誌連結")
    if memory.remember_installed():
        changed, other = memory.install_journal_config()
        memory_hooks.install(enabling=True)
        if changed:
            log_success(f"remember 日誌改存到 {memory.JOURNAL_TEMPLATE}")
            log_info(
                "各專案下次開會話時自動搬遷;要跨機器同步請在專案裡執行 memory adopt"
            )
        elif other:
            log_warn(f"remember 的 data_dir 已另外設定為 {other},未更動")
    if memory.codex_override_path().is_file():
        log_warn(f"{tilde(memory.codex_override_path())} 存在,Codex 可能讀不到共用規則")

    _offer_hosts()

    print()
    log_success("共用記憶已啟用;開新的 AI 會話後生效")
    log_info(f"保存記憶:{ENTRYPOINT} memory push")
    return 0


_HOST_LABELS = {"codex": "Codex", "agy": "Antigravity"}
_TRUST_HINT = "開一次 codex,輸入 /hooks 看過 remember 的 hook 就算信任"


def _report_hosts() -> None:
    """One line per host: the journal only records what remember captures."""
    from .. import remember_hosts as hosts

    for host, label in _HOST_LABELS.items():
        if not hosts.available(host):
            continue
        found = hosts.state(host)
        if not found.installed:
            if found.detail:
                log_warn(f"{label} 的 remember:{found.detail}")
            else:
                log_info(
                    f"{label} 尚未裝 remember,它的工作不會進專案日誌"
                    f"({ENTRYPOINT} memory enable {host})"
                )
        elif found.trusted is False:
            log_warn(f"{label} 已裝 remember {found.version},但 hook 還沒信任:{_TRUST_HINT}")
        else:
            extra = f",{found.detail}" if found.detail else ""
            log_success(f"{label} 的 remember 已安裝({found.version}{extra})")


def _offer_hosts() -> None:
    """Ask once per host that lacks the capture; the GUI has its own switches."""
    import sys

    from .. import remember_hosts as hosts
    from ..console import confirm

    if not sys.stdin.isatty():
        return
    for host, label in _HOST_LABELS.items():
        try:
            if not hosts.available(host) or hosts.state(host).installed:
                continue
            # 裝第三方擷取不該被 --force 一律答 yes;要人親口說好
            if not confirm(
                f"在 {label} 安裝 remember,讓它的工作也進專案日誌?", forceable=False,
            ):
                continue
            for line in hosts.install(host):
                (log_warn if "/hooks" in line else log_success)(line)
        except RuntimeError as exc:
            log_warn(f"{label} 這邊沒裝成:{exc}")


def _host(command: str, host: str) -> int:
    from .. import remember_hosts as hosts

    label = _HOST_LABELS[host]
    log_header(f"{'Enable' if command == 'enable' else 'Disable'} remember on {label}")
    if command == "enable" and not hosts.available(host):
        log_error(f"這台沒有 {label} 的指令,沒東西可以裝")
        return 1
    lines = hosts.install(host) if command == "enable" else hosts.remove(host)
    for line in lines:
        (log_warn if "/hooks" in line else log_success)(line)
    if not lines:
        log_info("沒有需要改的")
    return 0


def _disable() -> int:
    log_header("Disable shared memory")
    memory_hooks.install(enabling=False)
    for path in memory.instruction_paths():
        if memory.remove_block(path):
            log_success(f"移除規則區塊 {tilde(path)}")
    if memory.remove_agy_rules():
        log_success(f"移除 {tilde(memory.agy_rules_path())}")
    if memory.remove_link():
        log_success(f"移除連結 {tilde(MEMORY_LINK)}")
    if memory.remove_journal_config():
        log_success("remember 日誌位置改回各專案的 .remember")
        log_info(
            f"已搬過的日誌仍在 {tilde(memory.journal_root())} 與各專案記憶的 journal/"
        )
    log_info(f"記憶內容保留在 {tilde(memory.memory_dir())}")
    return 0


def _adopt(project: Path | None = None) -> int:
    log_header("Adopt project journal")
    if not memory.remember_installed():
        log_error("找不到 remember plugin,沒有日誌可以接管")
        return 1
    state, other = memory.journal_config_state()
    if state == "other":
        raise RuntimeError(f"remember 的 data_dir 已另外設定為 {other},先移除再重試")
    memory.ensure_index()
    ok, detail = memory.create_link()
    if not ok:
        raise RuntimeError(f"無法建立連結 {tilde(MEMORY_LINK)}:{detail}")
    changed, other = memory.install_journal_config()
    if changed:
        log_success(f"remember 日誌改存到 {memory.JOURNAL_TEMPLATE}")
    elif other:
        raise RuntimeError(f"remember 的 data_dir 已另外設定為 {other},先移除再重試")
    root = memory.project_root(project)
    lines = memory.adopt_journal(root)
    if not lines:
        log_success("這個專案的日誌已經在共用記憶裡")
        return 0
    for line in lines:
        log_success(line)
    key = memory.project_key(root)
    log_info(f"日誌位置:{tilde(memory.project_journal_dir(key))}")
    log_info(
        f"保存:{ENTRYPOINT} memory push;其他機器在同一專案執行 {ENTRYPOINT} memory adopt"
    )
    return 0


def _release(project: Path | None = None) -> int:
    log_header("Release project journal")
    lines = memory.release_journal(memory.project_root(project))
    if not lines:
        log_info("這個專案的日誌本來就沒有接進共用記憶")
        return 0
    for line in lines:
        log_success(line)
    return 0


def _path(flags: list[str]) -> int:
    state = memory.inspect()
    if flags == ["--global"]:
        print(state.directory)
    elif flags == ["--project"]:
        print(state.project_dir)
    else:
        print(f"global: {state.directory}")
        stability = "stable" if state.project.stable else "local-only"
        print(f"project: {state.project_dir} ({state.project.key}, {stability})")
    return 0


def _adopt_scan(args: list) -> int:
    """Adopt every project under a root, so nobody visits them one at a time."""
    from ..console import confirm

    if len(args) > 1:
        log_error(f"Usage: {ENTRYPOINT} memory adopt --scan [目錄]")
        return 1
    root = Path(args[0]).expanduser() if args else Path.home()
    if not root.is_dir():
        log_error(f"找不到這個目錄:{root}")
        return 1

    log_header("尚未同步的專案")
    pending = memory.unadopted_below(root)
    if not pending:
        log_info(f"{tilde(root)} 底下每個有日誌的專案都同步了")
        return 0
    for project in pending:
        state, _ = memory.journal_state(project)
        print(f"  {state:<7} {tilde(project)}")
    log_info(f"共 {len(pending)} 個,日誌會搬進共用資料庫並留下連結")
    if not confirm("全部同步?"):
        log_info("沒有變更")
        return 0

    failed = []
    for project in pending:
        result = execute("adopt", project)
        if result.code != 0:
            failed.append(project)
    if failed:
        # 一個失敗不該讓其他的白做,但要講清楚哪幾個沒成功
        log_warn(f"{len(failed)} 個沒有成功:")
        for project in failed:
            print(f"  {tilde(project)}")
        return 1
    log_success(f"{len(pending)} 個專案的日誌都同步了")
    return 0
