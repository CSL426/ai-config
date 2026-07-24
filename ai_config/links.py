"""Platform link strategies and ownership-safe Windows copy fallback."""

import hashlib
import json
import ntpath
import os
import shutil
import uuid
from pathlib import Path

from .console import log_success, log_warn
from .fsops import merge_missing_tree, mirror_dir
from .paths import (
    AGY_CANONICAL_SKILLS,
    AGY_HOME,
    AGY_LEGACY_SKILLS,
    EXCLUDED_FILES,
    NATIVE_WINDOWS,
    WINDOWS_MODE,
)
from .safety import (
    assert_no_symlinks,
    assert_root_not_reparse,
    assert_safe_write_target,
    is_reparse_point,
)

AGY_MARKER = ".ai-config-skills-mirror"
AGY_STATE = ".ai-config-skills-state.json"
_KINDS = {"file", "directory", "junction"}


def _path_exists(path: Path) -> bool:
    return path.exists() or is_reparse_point(path)


def _safe_leaf(name: str) -> bool:
    return (
        bool(name)
        and name not in (".", "..")
        and Path(name).name == name
        and not Path(name).is_absolute()
        and "/" not in name
        and "\\" not in name
        and ":" not in name
    )


def _normalize_windows_path_text(path: "Path | str") -> str:
    text = os.fspath(path).replace("/", "\\")
    if text.casefold().startswith("\\\\?\\unc\\"):
        text = "\\\\" + text[8:]
    elif text.casefold().startswith(("\\\\?\\", "\\??\\")):
        text = text[4:]
    return ntpath.normcase(ntpath.normpath(ntpath.abspath(text)))


def _path_identity(left: Path, right: Path) -> bool:
    if WINDOWS_MODE:
        try:
            return os.path.samefile(left, right)
        except (OSError, ValueError):
            pass
        return _normalize_windows_path_text(left) == _normalize_windows_path_text(
            right
        )
    return os.path.abspath(left) == os.path.abspath(right)


def _reparse_target(path: Path) -> Path:
    try:
        target = Path(os.readlink(path))
    except OSError as exc:
        raise RuntimeError(f"Cannot read reparse point target: {path}") from exc
    if not target.is_absolute():
        target = path.parent / target
    return Path(os.path.abspath(target))


def _assert_reparse_target(path: Path, expected: Path) -> None:
    if not _path_identity(_reparse_target(path), expected):
        raise RuntimeError(f"Reparse point target mismatch: {path}")


def file_fingerprint(path: Path) -> str:
    assert_no_symlinks(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def directory_fingerprint(path: Path) -> str:
    assert_no_symlinks(path)
    records = []
    for file_path in path.rglob("*"):
        if not file_path.is_file() or file_path.name in EXCLUDED_FILES:
            continue
        relative = file_path.relative_to(path).as_posix()
        records.append(f"{relative}\0{file_fingerprint(file_path)}")
    records.sort(key=str.casefold if WINDOWS_MODE else None)
    return hashlib.sha256("\n".join(records).encode()).hexdigest()


def _path_fingerprint(path: Path, kind: str) -> str:
    if kind == "file":
        return file_fingerprint(path)
    if kind == "directory":
        return directory_fingerprint(path)
    raise RuntimeError(f"Cannot fingerprint path kind: {kind}")


def read_ownership_state(path: Path) -> dict[str, dict[str, object]]:
    if not path.is_file() or is_reparse_point(path):
        if is_reparse_point(path):
            log_warn(f"Ignoring reparse point ownership state: {path}")
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("version") != 1:
            log_warn(f"Ignoring unsupported ownership state: {path}")
            return {}
        entries: dict[str, dict[str, object]] = {}
        for entry in document.get("entries", []):
            if not isinstance(entry, dict):
                continue
            relative = entry.get("path")
            if (
                entry.get("version") != 1
                or not isinstance(relative, str)
                or not _safe_leaf(relative)
                or not isinstance(entry.get("source"), str)
                or not entry.get("source")
                or entry.get("kind") not in _KINDS
            ):
                continue
            entries[relative] = entry
        return entries
    except (OSError, ValueError, TypeError):
        log_warn(f"Ignoring unreadable ownership state: {path}")
        return {}


def _write_atomic(path: Path, content: str) -> None:
    assert_safe_write_target(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_ownership_state(path: Path, entries: list[dict[str, object]]) -> None:
    content = json.dumps(
        {"version": 1, "entries": entries},
        ensure_ascii=False,
        indent=2,
    )
    _write_atomic(path, content + "\n")


def new_ownership_entry(
    relative_path: str,
    source: Path,
    destination: Path,
    kind: str,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "version": 1,
        "path": relative_path,
        "source": os.path.abspath(source),
        "kind": kind,
    }
    if kind == "junction":
        entry["target"] = os.path.abspath(source)
    else:
        entry["fingerprint"] = _path_fingerprint(destination, kind)
    return entry


def ownership_record_matches(
    record: "dict[str, object] | None",
    source: Path,
    destination: Path,
) -> bool:
    if record is None:
        return False

    def changed() -> bool:
        log_warn(f"Fallback ownership/content changed: {destination}")
        return False

    if not _path_identity(Path(str(record["source"])), source):
        return changed()
    if not _path_exists(destination):
        return changed()
    kind = str(record["kind"])
    if is_reparse_point(destination):
        if kind != "junction":
            raise RuntimeError(f"Reparse point ownership mismatch: {destination}")
        _assert_reparse_target(destination, source)
        return True
    if kind == "junction":
        return changed()
    actual_kind = "directory" if destination.is_dir() else "file"
    if actual_kind != kind:
        return changed()
    if _path_fingerprint(destination, actual_kind) != record.get("fingerprint"):
        return changed()
    return True


def _assert_recorded_destinations_safe(
    state: dict[str, dict[str, object]],
    canonical_root: Path,
    destination_root: Path,
) -> None:
    for relative, record in state.items():
        source = canonical_root / relative
        destination = destination_root / relative
        recorded_source = Path(str(record["source"]))
        allowed_sources = [source]
        if relative == "skills":
            allowed_sources.append(AGY_LEGACY_SKILLS)
        if not any(
            _path_identity(recorded_source, allowed) for allowed in allowed_sources
        ):
            raise RuntimeError(f"Recorded fallback source mismatch: {destination}")
        if record["kind"] == "junction":
            if not _path_exists(destination) or not is_reparse_point(destination):
                raise RuntimeError(
                    f"Recorded junction destination mismatch: {destination}"
                )
            _assert_reparse_target(destination, recorded_source)
        elif is_reparse_point(destination):
            raise RuntimeError(f"Reparse point ownership mismatch: {destination}")


def assert_agy_fallback_destination_safe() -> None:
    if not _path_exists(AGY_HOME):
        return
    assert_root_not_reparse(AGY_HOME, "Antigravity CLI root")
    if not AGY_HOME.is_dir():
        return
    state_path = AGY_HOME / AGY_STATE
    marker = AGY_HOME / AGY_MARKER
    assert_safe_write_target(state_path)
    assert_safe_write_target(marker)
    state = read_ownership_state(state_path)
    _assert_recorded_destinations_safe(
        state, AGY_CANONICAL_SKILLS.parent, AGY_HOME
    )


def preflight_windows_links(tools: list[str]) -> None:
    if not WINDOWS_MODE:
        return
    if "agy" in tools:
        assert_agy_fallback_destination_safe()


def _remove_owned_path(path: Path) -> None:
    if not _path_exists(path):
        return
    if is_reparse_point(path):
        if path.is_dir() and NATIVE_WINDOWS:
            path.rmdir()
        else:
            path.unlink()
    elif path.is_dir():
        assert_no_symlinks(path)
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy_owned_path(source: Path, destination: Path) -> str:
    if source.is_dir():
        mirror_dir(source, destination)
        return "directory"
    assert_safe_write_target(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return "file"


def prepare_agy_canonical_skills() -> None:
    canonical = AGY_CANONICAL_SKILLS
    legacy = AGY_LEGACY_SKILLS
    if not legacy.is_dir() or is_reparse_point(legacy):
        canonical.mkdir(parents=True, exist_ok=True)
        return
    if merge_missing_tree(legacy, canonical, "legacy Antigravity"):
        log_warn(f"Migrated legacy Antigravity skills into: {canonical}")


def _try_create_junction(source: Path, destination: Path) -> bool:
    if (
        not NATIVE_WINDOWS
        or not source.is_dir()
        or os.environ.get("AI_CONFIG_FORCE_COPY_FALLBACK") == "1"
    ):
        return False
    try:
        import _winapi

        destination.parent.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(source), str(destination))
        return True
    except (ImportError, OSError):
        log_warn(f"Could not create junction for {destination}; using copy fallback")
        return False


def sync_agy_skills_surface() -> None:
    canonical = AGY_CANONICAL_SKILLS
    if not canonical.is_dir():
        return
    assert_agy_fallback_destination_safe()
    AGY_HOME.mkdir(parents=True, exist_ok=True)
    destination = AGY_HOME / "skills"
    state_path = AGY_HOME / AGY_STATE
    marker = AGY_HOME / AGY_MARKER
    state = read_ownership_state(state_path)
    record = state.get("skills")
    destination_exists = _path_exists(destination)
    record_source = (
        Path(str(record["source"])) if record is not None else canonical
    )
    legacy_record = record is not None and _path_identity(
        record_source, AGY_LEGACY_SKILLS
    )

    if destination_exists and record is None:
        log_warn(f"Not replacing unmanaged Antigravity skills path: {destination}")
        return
    if not destination_exists and record is not None:
        log_warn(f"Fallback ownership/content changed: {destination}")
        write_ownership_state(state_path, [record])
        return
    if destination_exists and not ownership_record_matches(
        record, record_source, destination
    ):
        if record is not None:
            write_ownership_state(state_path, [record])
        return

    if destination_exists and legacy_record:
        _remove_owned_path(destination)
        destination_exists = False

    is_existing_junction = (
        destination_exists and record and record["kind"] == "junction"
    )
    if is_existing_junction or (
        not destination_exists
        and _try_create_junction(canonical, destination)
    ):
        kind = "junction"
    else:
        kind = _copy_owned_path(canonical, destination)
    entry = new_ownership_entry("skills", canonical, destination, kind)
    write_ownership_state(state_path, [entry])
    _write_atomic(marker, "skills\n")


def _ensure_agy_unix_link() -> None:
    link = AGY_HOME / "skills"
    target = AGY_CANONICAL_SKILLS
    target.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        current_target = _reparse_target(link)
        if _path_identity(current_target, target):
            return
        if _path_identity(current_target, AGY_LEGACY_SKILLS):
            link.unlink()
        else:
            log_warn(f"Not replacing existing symlink: {link} -> {os.readlink(link)}")
            return
    if link.exists():
        log_warn(f"Not replacing existing agy skills dir: {link} (expected symlink -> {target})")
        return
    AGY_HOME.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    log_success("linked ~/.gemini/antigravity-cli/skills -> ~/.gemini/config/skills")


def ensure_agy_shared_links() -> None:
    if WINDOWS_MODE:
        sync_agy_skills_surface()
    else:
        _ensure_agy_unix_link()
