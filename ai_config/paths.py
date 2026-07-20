import os
import sys
from pathlib import Path

from .config import ConfigError, configured_data_repo, default_data_repo

HOME = Path(os.environ.get("HOME", str(Path.home())))

CONFIG_ERROR = None
try:
    configured_repo = configured_data_repo()
except ConfigError as exc:
    configured_repo = None
    CONFIG_ERROR = str(exc)

if configured_repo is not None:
    SCRIPT_DIR = configured_repo
else:
    parents_root = Path(__file__).resolve().parents[1]
    checkout_data_repo = (parents_root / "data").resolve()
    default_home_repo = default_data_repo().resolve()
    legacy_home_repo = (HOME / "ai-config").resolve()
    frozen = getattr(sys, "frozen", False)
    if not frozen and any(
        (parents_root / d).is_dir() for d in ("claude", "codex", "agy")
    ):
        SCRIPT_DIR = parents_root
    elif not frozen and any(
        (checkout_data_repo / d).is_dir() for d in ("claude", "codex", "agy")
    ):
        SCRIPT_DIR = checkout_data_repo
    elif any((default_home_repo / d).is_dir() for d in ("claude", "codex", "agy")):
        SCRIPT_DIR = default_home_repo
    elif any((legacy_home_repo / d).is_dir() for d in ("claude", "codex", "agy")):
        SCRIPT_DIR = legacy_home_repo
    else:
        SCRIPT_DIR = default_home_repo

WINDOWS_MODE = os.environ.get("AI_CONFIG_PLATFORM") == "windows" or os.name == "nt"
NATIVE_WINDOWS = os.name == "nt"
ENTRYPOINT = os.environ.get(
    "AI_CONFIG_ENTRYPOINT",
    ".\\ai-config.ps1" if WINDOWS_MODE else "./ai-config.sh",
)

CLAUDE_HOME = HOME / ".claude"
CODEX_HOME = HOME / ".codex"
AGY_HOME = HOME / ".gemini" / "antigravity-cli"

CODEX_CANONICAL_SKILLS = HOME / ".agents" / "skills"
CODEX_LEGACY_SKILLS = CODEX_HOME / "skills"
CODEX_SKILLS_MIGRATION_MARKER = ".ai-config-codex-skills-migrated"

# Antigravity 2.0 stores global skills here. AGY_HOME/skills points to this
# canonical store so the editor and CLI share one skills directory.
AGY_CANONICAL_SKILLS = HOME / ".gemini" / "config" / "skills"
AGY_LEGACY_SKILLS = HOME / ".gemini" / "antigravity" / "skills"

# All managed tools (order matters for init/apply/status)
ALL_TOOLS = ["claude", "codex", "agy"]

# Credential files to never copy
EXCLUDED_FILES = {
    ".credentials.json",
    "auth.json",
    "oauth_creds.json",
    "google_accounts.json",
    "trustedFolders.json",
}

BACKUP_BASE = HOME / ".ai-config-backup"
BACKUP_KEEP = 5

CLAUDE_MANAGED_FILES = ["CLAUDE.md", "mcp.json", "settings.json", "statusline.sh"]
CLAUDE_MANAGED_DIRS = ["rules", "agents", "commands"]

CLAUDE_BACKUP_PATHS = CLAUDE_MANAGED_FILES + CLAUDE_MANAGED_DIRS
CODEX_BACKUP_PATHS = ["AGENTS.md", "config.toml", "rules", "skills"]
AGY_BACKUP_PATHS = ["mcp_config.json", "settings.json", "skills", "plugins"]

MANIFEST_NAME = ".ai-config-managed"

TOOL_HOMES = {"claude": CLAUDE_HOME, "codex": CODEX_HOME, "agy": AGY_HOME}

# Claude source dir for projections; the `project` command temporarily points
# this at the live ~/.claude (mirrors CLAUDE_SOURCE_DIR in the bash version).
_claude_source_dir = SCRIPT_DIR / "claude"


def claude_source_dir() -> Path:
    return _claude_source_dir


def set_claude_source_dir(path: Path) -> None:
    global _claude_source_dir
    _claude_source_dir = path


def tool_home(tool: str) -> Path:
    return TOOL_HOMES[tool]


def codex_live_skills() -> Path:
    if os.path.lexists(CODEX_CANONICAL_SKILLS):
        return CODEX_CANONICAL_SKILLS
    if os.path.lexists(CODEX_LEGACY_SKILLS):
        return CODEX_LEGACY_SKILLS
    return CODEX_CANONICAL_SKILLS


def tilde(path: "Path | str") -> str:
    """Render a path with $HOME abbreviated to ~ (for log messages)."""
    text = str(path)
    home = str(HOME)
    if text.startswith(home):
        return "~" + text[len(home):]
    return text
