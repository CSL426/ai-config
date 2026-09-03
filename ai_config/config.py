"""Persistent user configuration for the standalone CLI."""

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

CONFIG_ENV = "AI_CONFIG_CONFIG"
DATA_REPO_ENV = "AI_CONFIG_REPO"
REMOTE_PROVIDERS = frozenset({"git", "gdrive"})
GDRIVE_FOLDER_DEFAULT = "ai-config"


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
    return _home(environ) / ".acg" / "data"


def legacy_default_data_repo(
    environ: "dict[str, str] | None" = None,
) -> Path:
    """1.0.31 以前的預設位置;未設定 config 的機器仍要找得到它。"""
    return _home(environ) / "ai-config" / "data"


def load_config(path: "Path | None" = None) -> dict[str, Any]:
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
    remote_provider = payload.get("remote_provider")
    if remote_provider is not None and not isinstance(remote_provider, str):
        raise ConfigError(f"remote_provider must be a string in {target}")
    if remote_provider is not None and remote_provider not in REMOTE_PROVIDERS:
        raise ConfigError(
            f"remote_provider must be git or gdrive in {target}"
        )
    for key in ("gdrive_folder", "gdrive_folder_id"):
        if payload.get(key) is not None and not isinstance(payload[key], str):
            raise ConfigError(f"{key} must be a string in {target}")
    return payload


def normalize_gdrive_folder(value: "str | None") -> str:
    """Turn user input like `` Backups / ai-config `` into ``Backups/ai-config``.

    Segments are relative to My Drive; both slash styles are accepted because
    Windows users type backslashes by reflex.
    """
    if value is None:
        return GDRIVE_FOLDER_DEFAULT
    segments = []
    for raw_segment in value.replace("\\", "/").split("/"):
        segment = raw_segment.strip()
        if segment:
            segments.append(segment)
    if not segments:
        return GDRIVE_FOLDER_DEFAULT
    if any(segment in (".", "..") for segment in segments):
        raise ConfigError(f"Google Drive folder path is invalid: {value!r}")
    return "/".join(segments)


def configured_gdrive_folder(
    environ: "dict[str, str] | None" = None,
) -> str:
    value = load_config(config_path(environ)).get("gdrive_folder")
    return normalize_gdrive_folder(value if isinstance(value, str) else None)


def configured_gdrive_folder_id(
    environ: "dict[str, str] | None" = None,
) -> "str | None":
    value = load_config(config_path(environ)).get("gdrive_folder_id")
    return value if isinstance(value, str) and value else None


def configured_data_repo() -> "Path | None":
    override = os.environ.get(DATA_REPO_ENV)
    if override:
        return Path(override).expanduser().resolve()
    data_repo = load_config().get("data_repo")
    if not data_repo:
        return None
    return Path(data_repo).expanduser().resolve()


def configured_remote_provider() -> str:
    override = os.environ.get("AI_CONFIG_PROVIDER")
    if override:
        if override not in REMOTE_PROVIDERS:
            raise ConfigError("AI_CONFIG_PROVIDER must be git or gdrive")
        return override
    provider = load_config().get("remote_provider")
    return provider if isinstance(provider, str) and provider else "git"


def save_data_repo(
    data_repo: Path,
    path: "Path | None" = None,
    remote_provider: "str | None" = None,
    gdrive_folder: "str | None" = None,
    gdrive_folder_id: "str | None" = None,
) -> Path:
    if remote_provider is not None and remote_provider not in REMOTE_PROVIDERS:
        raise ConfigError("remote_provider must be git or gdrive")
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = {}
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current = loaded
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass

    current["data_repo"] = str(data_repo.resolve())
    if remote_provider == "gdrive":
        current["remote_provider"] = "gdrive"
    elif remote_provider == "git" and "remote_provider" in current:
        current["remote_provider"] = "git"
    if gdrive_folder is not None:
        current["gdrive_folder"] = normalize_gdrive_folder(gdrive_folder)
        # 資料夾 id 綁定 Drive 上的實體;使用者事後在 Drive 裡搬動資料夾也不會失聯
        if gdrive_folder_id:
            current["gdrive_folder_id"] = gdrive_folder_id
        else:
            current.pop("gdrive_folder_id", None)

    payload = json.dumps(
        current,
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
