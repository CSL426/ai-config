"""The journal capture on the other two hosts: Codex and Antigravity.

Claude Code installs the remember plugin through its own marketplace and
acg only points its journal at the shared notebook. Codex and Antigravity
read the same rule block and are told to read the same journal, but nothing
ever installed the capture there, so their sessions never reached it: the
project journal recorded one tool's work and called it the project's.

remember ships the other two hosts itself. Codex takes it as a plugin from
the author's marketplace; Antigravity has no per-plugin manifest, so its
hooks are one entry in the shared ~/.gemini/config/hooks.json. acg writes
and removes that entry on its own, so removal never depends on the plugin
still being present. Codex additionally reviews new hooks before it runs
them; that trust is Codex's own gate and is only detected here, never
written.
"""

import json
import shlex
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import memory_journal, memory_paths
from .paths import CODEX_HOME, HOME

CODEX_MARKETPLACE_REPO = "Digital-Process-Tools/claude-remember"
CODEX_MARKETPLACE = "remember-dev"
CODEX_PLUGIN = "remember"
CODEX_PLUGIN_REF = f"{CODEX_PLUGIN}@{CODEX_MARKETPLACE}"
AGY_HOOKS = HOME / ".gemini" / "config" / "hooks.json"
AGY_KEY = "remember"
# remember 的 Antigravity 轉接腳本;它自己的安裝器就是綁這三個事件
_AGY_EVENT_SCRIPTS = {
    "SessionStart": "agy-session-start-hook.sh",
    "PreInvocation": "agy-pre-invocation-hook.sh",
    "Stop": "agy-stop-hook.sh",
}
_AGY_TIMEOUT = 30


@dataclass
class HostState:
    installed: bool
    version: str = ""
    # None 表示這個 host 沒有信任這回事(Antigravity)
    trusted: "bool | None" = None
    detail: str = ""


def _version_key(name: str) -> tuple:
    parts = []
    for piece in name.split("."):
        parts.append((0, int(piece)) if piece.isdigit() else (1, piece))
    return tuple(parts)


def _newest_version_dir(root: Path) -> "Path | None":
    if not root.is_dir():
        return None
    candidates = [p for p in root.iterdir() if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: _version_key(p.name))


def claude_plugin_root() -> "Path | None":
    """Claude Code's installed remember, the scripts Antigravity is pointed at."""
    return _newest_version_dir(memory_journal.REMEMBER_PLUGIN_CACHE)


# --- Codex ---------------------------------------------------------------


def codex_homes() -> list[Path]:
    """Every Codex home on this machine, the default one first.

    A shell function can point CODEX_HOME at a different directory per
    account (~/.codex-work, ~/.codex-personal), and each keeps its own
    plugins and its own hook trust. Reporting only ~/.codex then describes
    a home the user may never actually run in.
    """
    found = [CODEX_HOME] if (CODEX_HOME / "config.toml").is_file() else []
    for path in sorted(HOME.glob(".codex-*")):
        if path.is_dir() and (path / "config.toml").is_file():
            found.append(path)
    return found or [CODEX_HOME]


def codex_config(home: "Path | None" = None) -> dict:
    path = (home or CODEX_HOME) / "config.toml"
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"讀不了 Codex 設定 {path}:{exc}") from exc


def codex_plugin_dir(home: "Path | None" = None) -> "Path | None":
    return _newest_version_dir(
        (home or CODEX_HOME) / "plugins" / "cache" / CODEX_MARKETPLACE / CODEX_PLUGIN
    )


def codex_trusted(config: dict) -> bool:
    """Whether Codex has reviewed remember's hooks; the key names the manifest."""
    state = config.get("hooks", {}).get("state", {})
    if not isinstance(state, dict):
        return False
    # 實際的 key 長這樣:remember@remember-dev:hooks/hooks.codex.json:session_end:0:0
    return any(
        key.startswith(f"{CODEX_PLUGIN_REF}:") and "hooks.codex.json" in key
        for key in state
    )


def codex_state(home: "Path | None" = None) -> HostState:
    try:
        config = codex_config(home)
    except RuntimeError as exc:
        return HostState(installed=False, detail=str(exc))
    plugins = config.get("plugins", {})
    installed = isinstance(plugins, dict) and CODEX_PLUGIN_REF in plugins
    plugin_dir = codex_plugin_dir(home)
    if installed and plugin_dir is None:
        return HostState(installed=False, detail="設定裡有但快取不見了,重新安裝")
    if not installed:
        return HostState(installed=False)
    return HostState(
        installed=True,
        version=plugin_dir.name,
        trusted=codex_trusted(config),
    )


def _run_codex(*args: str) -> subprocess.CompletedProcess:
    binary = shutil.which("codex")
    if binary is None:
        raise RuntimeError("找不到 codex 指令,Codex 這邊先跳過")
    result = subprocess.run(
        [binary, "plugin", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=300,
    )
    if result.returncode != 0:
        lines = [line.strip() for line in (result.stderr or result.stdout).splitlines() if line.strip()]
        # clap 把原因印在 Usage 前面;只取最後幾行會剩下用法說明,看不出哪裡錯
        reason = [line for line in lines if line.lower().startswith("error")] or lines[-3:]
        raise RuntimeError(
            f"codex plugin {' '.join(args)} 失敗:" + " / ".join(reason)
        )
    return result


def _codex_plugin_commands() -> frozenset:
    """The verbs this Codex's `plugin` takes; empty when it has no plugins at all.

    Codex grew plugins around 0.15x (0.77 has none) and renamed install to
    add along the way, so a machine one version behind refused `add`.
    """
    binary = shutil.which("codex")
    if binary is None:
        raise RuntimeError("找不到 codex 指令,Codex 這邊先跳過")
    probe = subprocess.run(
        [binary, "plugin", "--help"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, timeout=60,
    )
    if probe.returncode != 0:
        return frozenset()
    return _help_commands(probe.stdout)


def _help_commands(text: str) -> frozenset:
    commands, inside = set(), False
    for line in text.splitlines():
        if line.strip() == "Commands:":
            inside = True
            continue
        if inside:
            if not line.startswith(" ") or not line.strip():
                break
            commands.add(line.split()[0])
    return frozenset(commands)


def _verb(commands: frozenset, *choices: str) -> str:
    found = next((verb for verb in choices if verb in commands), None)
    if found is None:
        raise RuntimeError(f"這台 Codex 的 plugin 沒有 {' 或 '.join(choices)} 子指令:{', '.join(sorted(commands))}")
    return found


def _codex_from_npm() -> bool:
    from .autoupdate import _is_npm_install

    binary = shutil.which("codex")
    return bool(binary) and _is_npm_install(binary)


def codex_version() -> str:
    binary = shutil.which("codex")
    if binary is None:
        return ""
    result = subprocess.run(
        [binary, "--version"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, timeout=60,
    )
    return result.stdout.strip() or result.stderr.strip()


def install_codex() -> list[str]:
    """Add the author's marketplace once, then the plugin; both idempotent."""
    commands = _codex_plugin_commands()
    # 0.125 的 plugin 只有 marketplace:先檢查,免得加了 marketplace 才發現裝不了
    if not commands & {"add", "install"}:
        raise RuntimeError(
            f"這台的 Codex({codex_version() or '版本不明'})還不能安裝 plugin,"
            "要先升級 Codex 才裝得了 remember"
            + ("(這台是 npm 安裝的,建議改用官方獨立安裝版)" if _codex_from_npm() else "")
        )
    lines = []
    marketplaces = codex_config().get("marketplaces", {})
    if not (isinstance(marketplaces, dict) and CODEX_MARKETPLACE in marketplaces):
        _run_codex("marketplace", "add", CODEX_MARKETPLACE_REPO)
        lines.append(f"Codex 加入 marketplace {CODEX_MARKETPLACE}")
    if not codex_state().installed:
        _run_codex("add" if "add" in commands else "install", CODEX_PLUGIN_REF)
        lines.append(f"Codex 安裝 remember({CODEX_PLUGIN_REF})")
    state = codex_state()
    if state.installed and not state.trusted:
        lines.append(
            "Codex 會先審核新 hook 才執行:開一次 codex,輸入 /hooks 看過 remember 的 hook 就算信任"
        )
    return lines


def remove_codex() -> list[str]:
    lines = []
    if codex_state().installed or codex_plugin_dir() is not None:
        _run_codex(_verb(_codex_plugin_commands(), "remove", "uninstall"), CODEX_PLUGIN_REF)
        lines.append("Codex 移除 remember")
    marketplaces = codex_config().get("marketplaces", {})
    if isinstance(marketplaces, dict) and CODEX_MARKETPLACE in marketplaces:
        _run_codex("marketplace", "remove", CODEX_MARKETPLACE)
        lines.append(f"Codex 移除 marketplace {CODEX_MARKETPLACE}")
    return lines


# --- Antigravity ----------------------------------------------------------


def agy_entry(plugin_root: Path) -> dict:
    """The one key remember owns in the shared hooks file, same shape it writes."""
    entry: dict = {"enabled": True}
    for event, script in _AGY_EVENT_SCRIPTS.items():
        # 正斜線在每個平台的 bash 都認得;整段 quote 才擋得住路徑裡的引號
        path = str(plugin_root / "scripts" / script).replace("\\", "/")
        entry[event] = [{
            "type": "command",
            "command": f"bash {shlex.quote(path)}",
            "timeout": _AGY_TIMEOUT,
        }]
    return entry


def _load_agy_hooks() -> dict:
    if not AGY_HOOKS.is_file():
        return {}
    try:
        data = json.loads(AGY_HOOKS.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"讀不了 Antigravity 的 hooks 檔 {AGY_HOOKS}:{exc}") from exc
    if not isinstance(data, dict):
        # 別的外掛的項目可能在裡面,壞了就不能拿空的蓋掉
        raise RuntimeError(f"Antigravity 的 hooks 檔不是物件,先手動處理:{AGY_HOOKS}")  # noqa: TRY004
    return data


def _write_agy_hooks(data: dict) -> None:
    memory_paths.assert_plain_path(AGY_HOOKS, directory=False)
    AGY_HOOKS.parent.mkdir(parents=True, exist_ok=True)
    memory_paths._write_text_atomic(
        AGY_HOOKS, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def _agy_scripts_missing(entry: dict) -> list[str]:
    missing = []
    for event in _AGY_EVENT_SCRIPTS:
        for hook in entry.get(event, []) or []:
            command = hook.get("command", "") if isinstance(hook, dict) else ""
            words = shlex.split(command) if command else []
            if len(words) >= 2 and not Path(words[1]).is_file():
                missing.append(words[1])
    return missing


def agy_state() -> HostState:
    try:
        data = _load_agy_hooks()
    except RuntimeError as exc:
        return HostState(installed=False, detail=str(exc))
    entry = data.get(AGY_KEY)
    if not isinstance(entry, dict):
        return HostState(installed=False)
    missing = _agy_scripts_missing(entry)
    if missing:
        return HostState(
            installed=True,
            detail="指向的腳本不存在(remember 換版了?重跑 enable 會更新路徑)",
        )
    root = Path(shlex.split(entry["SessionStart"][0]["command"])[1]).parent.parent
    return HostState(installed=True, version=root.name)


def install_agy() -> list[str]:
    root = claude_plugin_root()
    if root is None:
        raise RuntimeError("Claude Code 這邊沒有 remember plugin,Antigravity 沒有腳本可以綁")
    for script in _AGY_EVENT_SCRIPTS.values():
        if not (root / "scripts" / script).is_file():
            raise RuntimeError(f"這版 remember 沒有 Antigravity 的轉接腳本:{script}")
    data = _load_agy_hooks()
    wanted = agy_entry(root)
    if data.get(AGY_KEY) == wanted:
        return []
    data[AGY_KEY] = wanted
    _write_agy_hooks(data)
    return [f"Antigravity 的 hooks 加入 remember(腳本來自 {root.name})"]


def remove_agy() -> list[str]:
    data = _load_agy_hooks()
    if AGY_KEY not in data:
        return []
    del data[AGY_KEY]
    _write_agy_hooks(data)
    return ["Antigravity 的 hooks 移除 remember"]


HOSTS = ("codex", "agy")
_BINARIES = {"codex": "codex", "agy": "agy"}


def available(host: str) -> bool:
    """Whether that CLI exists here at all; a host that is absent is not a gap."""
    return shutil.which(_BINARIES[host]) is not None


def state(host: str) -> HostState:
    """One line per host; for Codex that means every home this machine has."""
    if host != "codex":
        return agy_state()
    homes = codex_homes()
    if len(homes) == 1:
        return codex_state(homes[0])
    states = [(home, codex_state(home)) for home in homes]
    missing = [home.name for home, found in states if not found.installed]
    untrusted = [
        home.name for home, found in states
        if found.installed and found.trusted is False
    ]
    first = states[0][1]
    if missing:
        return HostState(
            installed=False,
            detail=f"這台有 {len(homes)} 個 Codex home,尚未安裝:{', '.join(missing)}",
        )
    if untrusted:
        return HostState(
            installed=True, version=first.version, trusted=False,
            detail=f"尚未信任:{', '.join(untrusted)}",
        )
    return HostState(
        installed=True, version=first.version, trusted=True,
        detail=f"{len(homes)} 個 home 都已就緒",
    )


def install(host: str) -> list[str]:
    return install_codex() if host == "codex" else install_agy()


def remove(host: str) -> list[str]:
    return remove_codex() if host == "codex" else remove_agy()
