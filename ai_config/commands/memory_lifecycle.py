"""acg memory status, enable, disable, adopt and release.

Every change runs inside one transaction: preflight, snapshot, apply, and
restore on failure. Installing remember on the other hosts lives here too.
"""

import json
import shutil
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

from .. import memory_hooks, memory_index, memory_journal, memory_paths, safety
from ..console import log_error, log_header, log_info, log_success, log_warn
from ..locking import apply_lock
from ..paths import BACKUP_BASE, ENTRYPOINT, MEMORY_LINK, tilde


def _status() -> int:
    log_header("Shared memory")
    state = memory_index.inspect()
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
        log_warn(f"{tilde(memory_paths.codex_override_path())} 存在,會遮蔽 Codex 的共用規則")

    if state.index_unlisted:
        log_warn(
            f"有 {len(state.index_unlisted)} 則筆記沒有寫進索引,"
            "下次開會話不會被看到:"
        )
        for name in state.index_unlisted:
            print(f"  {memory_paths.TOPICS_NAME}/{name}")
    if state.index_dangling:
        log_warn(f"索引有 {len(state.index_dangling)} 個連結指向不存在的檔案:")
        for name in state.index_dangling:
            print(f"  {memory_paths.TOPICS_NAME}/{name}")

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
        pending = memory_index.unadopted_below(memory_paths.HOME)
    except OSError:
        return
    here = memory_paths.project_root()
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
    memory_paths.assert_plain_path(BACKUP_BASE, directory=True)
    folder = BACKUP_BASE / f"memory-{uuid.uuid4().hex}"
    folder.mkdir(parents=True)
    manifest = {}
    for number, path in enumerate(existing):
        memory_paths.assert_plain_path(path, directory=False)
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
    # 預覽就證明什麼都不用改時為 False;每晚的報告不該把它說成「已同步」
    changed: bool = True


class MemoryOperationError(RuntimeError):
    def __init__(self, message: str, backup: Path | None, recovery: bool):
        super().__init__(message)
        self.backup_path = str(backup) if backup is not None else None
        self.recovery_required = recovery


def execute(
    action: str, project: Path | None = None, *, lock_held: bool = False,
    old_journals: bool = True,
) -> MemoryExecutionResult:
    """Execute a validated lifecycle action; callers never change process cwd.

    old_journals=False leaves old-era .remember folders where they are:
    the nightly run adopts what sessions are writing now, not leftovers.
    """
    from ..memory_plan import plan

    operation = plan(action, project, old_journals=old_journals)
    if action in {"adopt", "release"} and not operation.changes:
        # 沒有事要做就不要建備份;預覽已經證明什麼都不會改
        for warning in operation.warnings:
            log_warn(warning)
        if action == "adopt":
            log_success("這個專案的日誌已經在共用記憶裡")
        else:
            log_info("這個專案的日誌本來就沒有接進共用記憶")
        return MemoryExecutionResult(code=0, changed=False)
    with _mutation(
        enabling=action == "enable", action=action, project=project,
        lock_held=lock_held, old_journals=old_journals,
    ) as result:
        if action == "enable":
            result.code = _enable()
        elif action == "disable":
            result.code = _disable()
        elif action == "adopt":
            result.code = _adopt(operation.project, operation.nested, old_journals=old_journals)
        else:
            result.code = _release(operation.project)
    return result


@contextmanager
def _mutation(
    *, enabling: bool, action: str | None = None,
    project: Path | None = None, lock_held: bool = False, old_journals: bool = True,
):
    # Preflight before creating the lock or snapshot: refused inputs stay intact.
    memory_paths.preflight_memory()
    memory_paths.preflight_rules(enabling=enabling)
    memory_paths.assert_plain_path(BACKUP_BASE, directory=True)
    with nullcontext() if lock_held else apply_lock():
        if action is not None:
            from ..memory_plan import plan

            operation = plan(action, project, old_journals=old_journals)
        else:
            operation = None
        memory_paths.preflight_memory()
        memory_paths.preflight_rules(enabling=enabling)
        paths = [memory_paths.rules_target(path) for path in memory_paths.instruction_paths()]
        paths.extend([
            memory_paths.agy_rules_path(),
            memory_paths.REMEMBER_USER_CONFIG,
            memory_paths.index_path(),
            memory_paths.memory_dir() / ".gitignore",
        ])
        if action in {"enable", "disable"}:
            paths.append(memory_hooks.settings_path())
        originals = {
            path: path.read_bytes() if path.is_file() else None
            for path in paths
        }
        link_state, _ = memory_paths.link_state()
        missing_directories = set()
        if operation is not None:
            for change in operation.changes:
                destination = Path(change["destination"])
                candidates = list(destination.parents)
                if change["operation"] == "mkdir":
                    candidates.append(destination)
                for directory in candidates:
                    if not directory.exists() and not safety.is_reparse_point(directory):
                        missing_directories.add(directory)
        backup_paths = list(paths)
        if action in {"adopt", "release"} and project is not None:
            root = memory_paths.project_root(project)
            journal_directories = [memory_journal.project_journal_dir(memory_paths.project_key(root))]
            if action == "adopt":
                journal_directories.extend([root / ".remember", memory_journal.journal_link(root)])
            for directory in journal_directories:
                if safety.is_reparse_point(directory):
                    continue
                memory_journal._check_journal_tree(directory)
                if directory.exists():
                    backup_paths.extend(p for p in directory.rglob("*") if p.is_file())
        snapshot = _backup(backup_paths)
        if snapshot is not None:
            state = {"memory_link": link_state}
            if project is not None:
                root = memory_paths.project_root(project)
                state["journal"] = {
                    "path": str(memory_journal.journal_link(root)),
                    "state": memory_journal.journal_state(root),
                }
            (snapshot / "links.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        result = MemoryExecutionResult(backup_path=str(snapshot) if snapshot else None)
        written: dict[Path, bytes | None] = {}
        observer = memory_paths.WRITE_OBSERVER.set(lambda path, content: written.__setitem__(path, content))
        try:
            yield result
        except (OSError, RuntimeError, ValueError) as failure:
            errors = []
            for path, content in originals.items():
                if path not in written:
                    continue
                try:
                    memory_paths.assert_plain_path(path, directory=False)
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
                current_link = memory_paths.link_state()[0]
                if current_link not in {"ok", "missing"}:
                    raise RuntimeError("共用連結已被外部修改")
                if link_state == "missing" and current_link == "ok":
                    memory_paths.remove_link()
                elif link_state == "ok" and current_link == "missing":
                    ok, detail = memory_paths.create_link()
                    if not ok:
                        raise RuntimeError(detail)
            except (OSError, RuntimeError) as exc:
                errors.append(f"無法還原共用連結:{exc}")
            for directory in sorted(missing_directories, key=lambda p: len(p.parts), reverse=True):
                try:
                    memory_paths.assert_plain_path(directory, directory=True)
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
            memory_paths.WRITE_OBSERVER.reset(observer)


def _enable() -> int:
    log_header("Enable shared memory")
    if memory_index.ensure_index():
        log_success(f"建立索引 {tilde(memory_paths.index_path())}")

    ok, detail = memory_paths.create_link()
    if ok:
        log_success(f"連結 {tilde(MEMORY_LINK)} -> {tilde(memory_paths.memory_dir())}")
    else:
        raise RuntimeError(f"無法建立連結 {tilde(MEMORY_LINK)}:{detail}")

    if memory_paths.install_block(memory_paths.live_rules_path()):
        log_success(f"規則區塊寫入 {tilde(memory_paths.live_rules_path())}")
    else:
        log_info("Claude 即時規則已有區塊")
    if memory_paths.source_rules_path().is_file():
        if memory_paths.install_block(memory_paths.source_rules_path()):
            log_success(f"規則區塊寫入 {tilde(memory_paths.source_rules_path())}")
            log_info(f"執行 {ENTRYPOINT} push claude 讓其他機器也拿到規則")
    else:
        log_warn(
            f"資料庫沒有 claude/CLAUDE.md,先執行 {ENTRYPOINT} init claude 再重跑一次"
        )
    if memory_paths.install_agy_rules():
        log_success(f"agy 規則寫入 {tilde(memory_paths.agy_rules_path())}")
    for path in memory_paths.instruction_paths():
        if (
            path not in (memory_paths.live_rules_path(), memory_paths.source_rules_path())
            and memory_paths.install_block(path)
        ):
            log_success(f"規則區塊寫入 {tilde(path)}")
    if memory_journal.ensure_journal_ignored():
        log_success("memory/.gitignore 排除本機的日誌連結")
    if memory_journal.remember_installed():
        changed, other = memory_journal.install_journal_config()
        memory_hooks.install(enabling=True)
        if changed:
            log_success(f"remember 日誌改存到 {memory_journal.JOURNAL_TEMPLATE}")
            log_info(
                "各專案下次開會話時自動搬遷;要跨機器同步請在專案裡執行 memory adopt"
            )
        elif other:
            log_warn(f"remember 的 data_dir 已另外設定為 {other},未更動")
    if memory_paths.codex_override_path().is_file():
        log_warn(f"{tilde(memory_paths.codex_override_path())} 存在,Codex 可能讀不到共用規則")
    _switch_reminder(True)

    _offer_hosts()

    print()
    log_success("共用記憶已啟用;開新的 AI 會話後生效")
    log_info(f"保存記憶:{ENTRYPOINT} memory push")
    return 0


def _switch_reminder(enabled: bool) -> None:
    """The handoff reminder rides along with shared memory.

    A broken reminder is not a reason to refuse shared memory; it is
    reported and left alone.
    """
    from .. import handoff_reminder as remind

    try:
        threshold = remind.follow_memory_locked(enabled)
    except (OSError, ValueError) as exc:
        log_warn(f"交接提醒沒有{'開啟' if enabled else '關閉'}:{exc}")
        return
    if threshold is None:
        return
    if enabled:
        log_success(f"交接提醒已開啟:Claude context 用量達 {threshold}% 時提醒寫交接")
        log_info(f"不需要的話:{ENTRYPOINT} memory handoff remind disable")
    else:
        log_success("移除交接提醒")


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
    _switch_reminder(False)
    for path in memory_paths.instruction_paths():
        if memory_paths.remove_block(path):
            log_success(f"移除規則區塊 {tilde(path)}")
    if memory_paths.remove_agy_rules():
        log_success(f"移除 {tilde(memory_paths.agy_rules_path())}")
    if memory_paths.remove_link():
        log_success(f"移除連結 {tilde(MEMORY_LINK)}")
    if memory_journal.remove_journal_config():
        log_success("remember 日誌位置改回各專案的 .remember")
        log_info(
            f"已搬過的日誌仍在 {tilde(memory_paths.journal_root())} 與各專案記憶的 journal/"
        )
    log_info(f"記憶內容保留在 {tilde(memory_paths.memory_dir())}")
    return 0


def _adopt(
    project: Path | None = None, nested: Path | None = None, *, old_journals: bool = True,
) -> int:
    log_header("Adopt project journal")
    if not memory_journal.remember_installed():
        log_error("找不到 remember plugin,沒有日誌可以接管")
        return 1
    state, other = memory_journal.journal_config_state()
    if state == "other":
        raise RuntimeError(f"remember 的 data_dir 已另外設定為 {other},先移除再重試")
    memory_index.ensure_index()
    ok, detail = memory_paths.create_link()
    if not ok:
        raise RuntimeError(f"無法建立連結 {tilde(MEMORY_LINK)}:{detail}")
    changed, other = memory_journal.install_journal_config()
    if changed:
        log_success(f"remember 日誌改存到 {memory_journal.JOURNAL_TEMPLATE}")
    elif other:
        raise RuntimeError(f"remember 的 data_dir 已另外設定為 {other},先移除再重試")
    root = memory_paths.project_root(project)
    lines = memory_journal.adopt_journal(root, old_journals=old_journals)
    if nested is not None:
        lines.extend(memory_journal.absorb_nested_journal(nested, root, old_journals=old_journals))
    if not lines:
        log_success("這個專案的日誌已經在共用記憶裡")
        return 0
    for line in lines:
        log_success(line)
    key = memory_paths.project_key(root)
    log_info(f"日誌位置:{tilde(memory_journal.project_journal_dir(key))}")
    log_info(
        f"保存:{ENTRYPOINT} memory push;其他機器在同一專案執行 {ENTRYPOINT} memory adopt"
    )
    return 0


def _release(project: Path | None = None) -> int:
    log_header("Release project journal")
    lines = memory_journal.release_journal(memory_paths.project_root(project))
    if not lines:
        log_info("這個專案的日誌本來就沒有接進共用記憶")
        return 0
    for line in lines:
        log_success(line)
    return 0


def adopt_touched(root: "Path | None" = None) -> list:
    """Adopt every project under home that already has a journal. Lines to log.

    A journal only exists where a Claude, Codex or Antigravity session has
    worked, so that is the line between a project and any directory. Five
    such projects on one machine were found only by a manual scan, weeks
    after their notes should have reached the other machines.

    A project without a git remote is listed, not adopted: its key is the
    directory name, and two machines' unrelated ~/test would share one.
    """
    import os

    if os.environ.get("AI_CONFIG_NO_AUTO_ADOPT"):
        return []
    # 這台沒啟用共用記憶時什麼都不做,不然每晚每個專案各報一次錯
    if (
        memory_paths.link_state()[0] != "ok"
        or memory_journal.journal_config_state()[0] != "ours"
    ):
        return []
    lines = []
    for project in memory_index.unadopted_below(root or memory_paths.HOME):
        # 只剩舊時代 .remember 的:設定好之後就不會再變,每晚不搬也不提;要的話手動 adopt
        if memory_journal.journal_state(project)[0] == "legacy":
            continue
        if not memory_paths.project_key(project).stable:
            lines.append(f"沒有 git 遠端,不自動同步:{tilde(project)}(要同步請手動 adopt)")
            continue
        try:
            result = execute("adopt", project, old_journals=False)
        except (OSError, RuntimeError, ValueError) as exc:
            lines.append(f"同步失敗:{tilde(project)}:{exc}")
            continue
        if result.code != 0:
            lines.append(f"同步失敗:{tilde(project)}")
        elif result.changed:
            lines.append(f"已同步專案日誌:{tilde(project)}")
    return lines


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
    pending = memory_index.unadopted_below(root)
    if not pending:
        log_info(f"{tilde(root)} 底下每個有日誌的專案都同步了")
        return 0
    for project in pending:
        state, _ = memory_journal.journal_state(project)
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
