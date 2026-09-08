"""acg memory: the shared notebook's status, enable, disable, path and push."""

import json
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from .. import memory
from ..console import log_error, log_header, log_info, log_success, log_warn
from ..locking import apply_lock
from ..paths import BACKUP_BASE, ENTRYPOINT, MEMORY_LINK, tilde

USAGE = f"Usage: {ENTRYPOINT} memory <status|enable|disable|adopt|release|path|push>"


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
    if command == "enable" and not rest:
        with _mutation(enabling=True):
            return _enable()
    if command == "disable" and not rest:
        with _mutation(enabling=False):
            return _disable()
    if command == "adopt" and not rest:
        with _mutation(enabling=False):
            return _adopt()
    if command == "release" and not rest:
        with _mutation(enabling=False):
            return _release()
    if command == "path" and set(rest) <= {"--global", "--project"}:
        return _path(rest)
    if command == "push":
        from .push import MEMORY_SCOPE, do_push

        allow_secrets = rest == ["--allow-secrets"]
        if rest and not allow_secrets:
            log_error(f"Usage: {ENTRYPOINT} memory push [--allow-secrets]")
            return 1
        return do_push(MEMORY_SCOPE, allow_secrets=allow_secrets)
    log_error(USAGE)
    return 1


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
    return 0


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


@contextmanager
def _mutation(*, enabling: bool):
    # Preflight before creating the lock or snapshot: refused inputs stay intact.
    memory.preflight_memory()
    memory.preflight_rules(enabling=enabling)
    memory.assert_plain_path(BACKUP_BASE, directory=True)
    with apply_lock():
        memory.preflight_memory()
        memory.preflight_rules(enabling=enabling)
        paths = [memory.rules_target(path) for path in memory.instruction_paths()]
        paths.extend([
            memory.agy_rules_path(),
            memory.REMEMBER_USER_CONFIG,
            memory.index_path(),
            memory.memory_dir() / ".gitignore",
        ])
        originals = {
            path: path.read_bytes() if path.is_file() else None
            for path in paths
        }
        link_state, _ = memory.link_state()
        snapshot = _backup(paths)
        try:
            yield
        except (OSError, RuntimeError, ValueError):
            for path, content in originals.items():
                try:
                    memory.assert_plain_path(path, directory=False)
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(content)
                except (OSError, RuntimeError) as exc:
                    log_error(f"無法還原 {tilde(path)}:{exc}")
            if link_state == "missing":
                memory.remove_link()
            elif link_state == "ok" and memory.link_state()[0] == "missing":
                memory.create_link()
            if snapshot:
                log_warn(f"操作失敗,原始檔案備份:{tilde(snapshot)}")
            raise


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
        if changed:
            log_success(f"remember 日誌改存到 {memory.JOURNAL_TEMPLATE}")
            log_info(
                "各專案下次開會話時自動搬遷;要跨機器同步請在專案裡執行 memory adopt"
            )
        elif other:
            log_warn(f"remember 的 data_dir 已另外設定為 {other},未更動")
    if memory.codex_override_path().is_file():
        log_warn(f"{tilde(memory.codex_override_path())} 存在,Codex 可能讀不到共用規則")

    print()
    log_success("共用記憶已啟用;開新的 AI 會話後生效")
    log_info(f"保存記憶:{ENTRYPOINT} memory push")
    return 0


def _disable() -> int:
    log_header("Disable shared memory")
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


def _adopt() -> int:
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
    root = memory.project_root()
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


def _release() -> int:
    log_header("Release project journal")
    lines = memory.release_journal(memory.project_root())
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
