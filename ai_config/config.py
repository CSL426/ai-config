"""Persistent user configuration for the standalone CLI."""

import json
import os
import sys
import tempfile
from pathlib import Path

CONFIG_ENV = "AI_CONFIG_CONFIG"
DATA_REPO_ENV = "AI_CONFIG_REPO"


class ConfigError(RuntimeError):
    """Raised when the persistent configuration cannot be read or written."""


def _home(environ: "dict[str, str] | None" = None) -> Path:
    environment = os.environ if environ is None else environ
    return Path(environment.get("HOME", str(Path.home()))).expanduser()


def config_path(environ: "dict[str, str] | None" = None) -> Path:
    environment = os.environ if environ is None else environ
    override = environment.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()

    home = _home(environment)
    windows_mode = (
        os.name == "nt" or environment.get("AI_CONFIG_PLATFORM") == "windows"
    )
    if windows_mode and environment.get("APPDATA"):
        return Path(environment["APPDATA"]) / "ai-config" / "config.json"
    if sys.platform == "darwin":
        return (
            home
            / "Library"
            / "Application Support"
            / "ai-config"
            / "config.json"
        )
    if environment.get("XDG_CONFIG_HOME"):
        return (
            Path(environment["XDG_CONFIG_HOME"]) / "ai-config" / "config.json"
        )
    return home / ".config" / "ai-config" / "config.json"


def default_data_repo(environ: "dict[str, str] | None" = None) -> Path:
    return _home(environ) / "ai-config" / "data"


def load_config(path: "Path | None" = None) -> dict[str, str]:
    target = path or config_path()
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError(
            f"Cannot read configuration file {target}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ConfigError(
            f"Configuration file must contain a JSON object: {target}"
        )
    data_repo = payload.get("data_repo")
    if data_repo is not None and not isinstance(data_repo, str):
        raise ConfigError(f"data_repo must be a string in {target}")
    return payload


def configured_data_repo() -> "Path | None":
    override = os.environ.get(DATA_REPO_ENV)
    if override:
        return Path(override).expanduser().resolve()
    data_repo = load_config().get("data_repo")
    if not data_repo:
        return None
    return Path(data_repo).expanduser().resolve()


def save_data_repo(data_repo: Path, path: "Path | None" = None) -> Path:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"data_repo": str(data_repo.resolve())},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.write("\n")
            temporary_path = Path(temporary.name)
        if os.name != "nt":
            temporary_path.chmod(0o600)
        os.replace(temporary_path, target)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise ConfigError(
            f"Cannot write configuration file {target}: {exc}"
        ) from exc
    return target
