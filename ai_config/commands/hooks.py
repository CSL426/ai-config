"""acg hooks: this machine's own hooks, and the ones shared with Codex and Antigravity."""

from ..console import log_error, log_header, log_info, log_success, log_warn
from ..paths import ENTRYPOINT

USAGE = (
    f"Usage: {ENTRYPOINT} hooks <list|enable <名稱>|disable <名稱>|"
    "share [<編號> --name <名稱> [--to both|codex|agy]]|unshare <名稱>>"
)


def run_hooks(args: list[str]) -> int:
    from .. import hooks

    action = args[0] if args else "list"
    rest = args[1:]
    try:
        if action == "list" and not rest:
            return _list()
        if action == "share":
            return _share(rest)
        if action == "unshare" and len(rest) == 1:
            return _unshare(rest[0])
        if action in {"enable", "disable"} and len(rest) == 1:
            hook = hooks.REGISTRY.get(rest[0])
            if hook is None:
                log_error(f"沒有這個 hook:{rest[0]}")
                log_info(f"可用的:{', '.join(hooks.REGISTRY)}")
                return 1
            log_header(f"{action.capitalize()} {hook.name}")
            on = hooks.configure(hook, action == "enable")
            if on:
                log_success(f"{hook.name} 已安裝;開新會話後生效")
            else:
                log_info(f"{hook.name} 已移除")
            return 0
    except (OSError, RuntimeError, ValueError) as exc:
        log_error(str(exc))
        return 1
    log_error(USAGE)
    return 1


def _list() -> int:
    from .. import hooks

    log_header("Claude Code hooks")
    for hook, on in hooks.states():
        mark = "✓" if on else "—"
        print(f"  {mark} {hook.name}")
        print(f"      {hook.summary}")
    log_info(
        "這些 hook 只裝在這台:它們指向本機的執行檔,所以不會同步到其他機器"
    )
    log_info(f"安裝:{ENTRYPOINT} hooks enable <名稱>")
    _list_shared()
    return 0


def _list_shared() -> None:
    from .. import shared_hooks

    log_header("Shared hooks(Claude Code 寫一次,同步給 Codex 與 Antigravity)")
    defined = shared_hooks.load_all()
    if not defined:
        log_info(f"還沒有共用的 hook;分享 Claude Code 的 hook:{ENTRYPOINT} hooks share")
        return
    untrusted = []
    for hook in defined:
        matcher = f" [{hook.matcher}]" if hook.matcher else ""
        print(f"  {hook.name}  {hook.event}{matcher}")
        print(f"      {hook.command}")
        for tool, label in (("codex", "Codex"), ("agy", "Antigravity")):
            reason = hook.unsupported(tool)
            print(f"      {label}:{'—  ' + reason if reason else '✓'}")
        if not hook.unsupported("codex"):
            untrusted += [
                home for home in shared_hooks.codex_homes()
                if (home / "hooks.json").is_file() and not shared_hooks.codex_trusted(home, hook)
            ]
    log_info(f"apply 時寫進各工具;改了定義或分享新的之後跑一次 {ENTRYPOINT} apply")
    for home in sorted(set(untrusted)):
        # 信任是 Codex 自己的安全關卡,acg 不替使用者按
        log_warn(f"Codex({home.name})還沒信任這些 hook:在那個帳號的 Codex 裡打 /hooks 檢視並信任")


def _share(rest: list[str]) -> int:
    from .. import hooks, shared_hooks

    document = hooks.read_settings()
    candidates = shared_hooks.user_hooks(document)
    if not rest:
        log_header("Claude Code 自己的 hook(可以分享的)")
        if not candidates:
            log_info("Claude Code 的 settings.json 裡沒有自訂的 command hook")
            return 0
        for index, (event, row, entry) in enumerate(candidates, 1):
            matcher = f" [{row.get('matcher')}]" if row.get("matcher") else ""
            print(f"  {index}. {event}{matcher}  {entry.get('command')}")
        log_info(f"分享第 n 個:{ENTRYPOINT} hooks share <n> --name <名稱> [--to both|codex|agy]")
        return 0
    options = _options(rest[1:])
    if options is None or not rest[0].isdigit() or "name" not in options:
        log_error(USAGE)
        return 1
    number = int(rest[0])
    if not 1 <= number <= len(candidates):
        log_error(f"沒有第 {number} 個 hook;先跑 {ENTRYPOINT} hooks share 看清單")
        return 1
    to = options.get("to", "both")
    if to not in shared_hooks.TARGETS:
        log_error(f"--to 只能是 {'|'.join(shared_hooks.TARGETS)}")
        return 1
    event, row, entry = candidates[number - 1]
    timeout = entry.get("timeout")
    hook = shared_hooks.SharedHook(
        name=options["name"], event=event, command=str(entry["command"]),
        matcher=str(row.get("matcher") or ""),
        timeout=timeout if isinstance(timeout, int) and timeout > 0 else 60, to=to,
    )
    if (shared_hooks.definitions_dir() / f"{hook.name}.json").exists():
        log_error(f"已經有叫 {hook.name} 的共用 hook;換個名字,或先 unshare")
        return 1
    path = shared_hooks.write_definition(hook)
    # 從此由 acg 投影回 Claude Code;原本那份留著會變成兩個一樣的 hook
    hooks.write_settings(shared_hooks.project_claude(shared_hooks.without_entry(document, entry)))
    log_success(f"已分享 {hook.name} → {path}")
    for tool, label in (("codex", "Codex"), ("agy", "Antigravity")):
        reason = hook.unsupported(tool)
        if reason:
            log_warn(f"{label} 不會收到:{reason}")
    log_info(f"跑 {ENTRYPOINT} apply 寫進 Codex 與 Antigravity,再 {ENTRYPOINT} push 同步到其他機器")
    return 0


def _unshare(name: str) -> int:
    from .. import hooks, shared_hooks

    hook = next((h for h in shared_hooks.load_all() if h.name == name), None)
    if hook is None:
        log_error(f"沒有叫 {name} 的共用 hook")
        return 1
    shared_hooks.delete_definition(name)
    # 放回 Claude Code 自己的 settings.json,變回只屬於 Claude Code 的 hook
    document = shared_hooks.project_claude(hooks.read_settings())
    entry = {"type": "command", "command": hook.command, "timeout": hook.timeout}
    row: dict = {"hooks": [entry]}
    if hook.matcher:
        row["matcher"] = hook.matcher
    document.setdefault("hooks", {}).setdefault(hook.event, []).append(row)
    hooks.write_settings(document)
    log_success(f"已取消分享 {name};它回到 Claude Code 的 settings.json")
    log_info(f"跑 {ENTRYPOINT} apply 從 Codex 與 Antigravity 移除,再 {ENTRYPOINT} push")
    return 0


def _options(words: list[str]) -> "dict[str, str] | None":
    options: dict[str, str] = {}
    while words:
        if len(words) < 2 or words[0] not in ("--name", "--to"):
            return None
        options[words[0][2:]] = words[1]
        words = words[2:]
    return options
