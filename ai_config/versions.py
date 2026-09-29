"""The installed executable is a link into a versions directory.

A single file at ~/.local/bin/ai-config cannot be replaced while it is
running: Windows refuses outright, which is why update there handed the
work to a detached PowerShell and returned 0 before the file had changed —
a success code meaning "delivered", not "done". Nothing could tell whether
an update had actually landed, a hook installed against the old path kept
pointing at it, and a bad release had no way back.

Claude Code solves this by never replacing the file. The name on PATH is a
link; each release lives in its own directory beside it; updating means
writing a new directory and moving the link. A running process keeps the
path it started with, the swap is atomic, and the previous release is
still on disk to go back to.

    ~/.local/bin/ai-config          -> ../share/ai-config/versions/1.0.99/app/ai-config
    ~/.local/share/ai-config/versions/1.0.99/app/ai-config
                                                /_internal/   (onedir build)
                                    /1.0.98/ai-config         (older onefile)

On Windows, where a symlink needs a privilege an ordinary account may not
hold, the stable path is a small launcher (launcher/ in this repository)
that runs the version named in versions/active. Switching is rewriting
that one line; nothing running is overwritten. Before the launcher it was
a copy of the real executable, replaced in place — a running onefile build
reads its own modules from that path, and died reading the new file.
"""

import hashlib
import os
import shutil
from pathlib import Path

from .paths import HOME, NATIVE_WINDOWS

VERSIONS_DIR_NAME = "versions"
APP_DIR = "app"
KEEP_VERSIONS = 5


def executable_name() -> str:
    return "ai-config.exe" if NATIVE_WINDOWS else "ai-config"


def bin_dir() -> Path:
    return Path(os.environ.get("AI_CONFIG_BIN_DIR", HOME / ".local" / "bin"))


def store_dir() -> Path:
    """Where released versions are kept, one directory each."""
    root = os.environ.get("AI_CONFIG_SHARE_DIR")
    base = Path(root) if root else HOME / ".local" / "share" / "ai-config"
    return base / VERSIONS_DIR_NAME


def version_dir(version: str) -> Path:
    return store_dir() / version


def version_binary(version: str) -> Path:
    """The executable of a version: app/ for a onedir build, else the old single file.

    A onedir build sits one level down on purpose. Releases before it
    switch to a version already on disk by copying versions/<v>/ai-config.exe
    onto PATH on Windows — which for a onedir exe, separated from its
    _internal directory, is a program that cannot start. Finding nothing
    there, they download the release's own installer instead.
    """
    onedir = version_dir(version) / APP_DIR / executable_name()
    if onedir.is_file():
        return onedir
    return version_dir(version) / executable_name()


def stable_path() -> Path:
    """The name on PATH — a link on POSIX, the launcher on Windows."""
    return bin_dir() / executable_name()


def is_managed(running: Path) -> bool:
    """Whether a running executable is one of the installed versions.

    On Windows the stable path is the launcher, a different file from the
    version it runs, so comparing the two no longer says "installed copy".
    """
    try:
        running.resolve().relative_to(store_dir().resolve())
    except (OSError, ValueError):
        return False
    return True


def _sort_key(name: str) -> tuple:
    parts = []
    for piece in name.split("."):
        parts.append((0, int(piece)) if piece.isdigit() else (1, piece))
    return tuple(parts)


def installed_versions() -> list[str]:
    """Versions on disk, oldest first."""
    root = store_dir()
    if not root.is_dir():
        return []
    found = [
        path.name for path in root.iterdir()
        if path.is_dir() and version_binary(path.name).is_file()
    ]
    return sorted(found, key=_sort_key)


def _active_marker() -> Path:
    return store_dir() / "active"


def active_version() -> "str | None":
    """Which version the stable path holds, or None when unmanaged.

    Where the stable path is a link, it answers this itself. Windows may
    not be allowed to make one and keeps a copy instead, which resolves
    to itself and says nothing about where it came from — so activate
    also records the name, and that record is what both platforms read.
    """
    path = stable_path()
    if not os.path.lexists(path):
        return None
    try:
        resolved = path.resolve()
        relative = resolved.relative_to(store_dir().resolve())
    except (OSError, ValueError):
        relative = None
    if relative is not None and relative.parts:
        return relative.parts[0]
    try:
        recorded = _active_marker().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    # 記錄可能落後於磁碟現況:那一版被刪掉就不算數
    return recorded if recorded and version_binary(recorded).is_file() else None


def place(version: str, source: Path) -> Path:
    """Put a downloaded executable into its own version directory."""
    target = version_dir(version)
    target.mkdir(parents=True, exist_ok=True)
    binary = target / executable_name()
    staged = target / f".{executable_name()}.new"
    shutil.copy2(source, staged)
    staged.chmod(0o755)
    os.replace(staged, binary)
    return binary


class NeedsInstaller(RuntimeError):
    """A switch that only the installer can make."""


def activate(version: str) -> bool:
    """Point the stable path at this version. False when it already does."""
    binary = version_binary(version)
    if not binary.is_file():
        raise FileNotFoundError(f"這個版本不在 {binary}")
    path = stable_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if active_version() == version and not NATIVE_WINDOWS:
        return False
    if NATIVE_WINDOWS:
        if not is_launcher(path):
            if binary.parent.name == APP_DIR:
                # onedir 的 exe 離開 _internal 就跑不起來,只能交給安裝腳本
                raise NeedsInstaller(f"{version} 需要啟動器,請重新執行安裝腳本")
            # 還沒換成啟動器的舊格局:照舊複製,檔案正在執行就換不掉
            staged = path.with_name(f".{path.name}.new")
            shutil.copy2(binary, staged)
            os.replace(staged, path)
        _record_active(version)
        return True
    staged = path.with_name(f".{path.name}.new")
    if os.path.lexists(staged):
        os.unlink(staged)
    # 相對連結:家目錄搬走或被掛到別的位置時仍然指得到
    try:
        target = os.path.relpath(binary, path.parent)
    except ValueError:
        target = str(binary)
    os.symlink(target, staged)
    os.replace(staged, path)
    _record_active(version)
    return True


def _launcher_marker() -> Path:
    return store_dir().parent / "launcher.sha256"


def is_launcher(path: Path) -> bool:
    """Whether the Windows stable path is the launcher rather than a copy.

    install.ps1 records the hash of the launcher it put on PATH; a copy of
    a real build left there by the older layout does not match it.
    """
    try:
        recorded = _launcher_marker().read_text(encoding="utf-8").strip().lower()
        if not recorded or not path.is_file():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == recorded
    except OSError:
        return False


def _record_active(version: str) -> None:
    marker = _active_marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    staged = marker.with_name(f".{marker.name}.new")
    staged.write_text(f"{version}\n", encoding="utf-8")
    os.replace(staged, marker)


def prune(keep: int = KEEP_VERSIONS) -> list[str]:
    """Drop the oldest versions, never the one in use."""
    current = active_version()
    versions = [v for v in installed_versions() if v != current]
    excess = len(versions) + (1 if current else 0) - keep
    removed = []
    for version in versions[:max(0, excess)]:
        shutil.rmtree(version_dir(version), ignore_errors=True)
        removed.append(version)
    return removed


def adopt_existing(version: str) -> bool:
    """Move a plain executable at the stable path into the versions layout.

    Machines installed before this layout have the real file sitting on
    PATH. Leaving it there would mean update keeps replacing a running
    file forever, so the first update moves it into its own directory and
    replaces the original with a link to it.
    """
    path = stable_path()
    if active_version() is not None or not path.is_file() or path.is_symlink():
        return False
    place(version, path)
    activate(version)
    return True
