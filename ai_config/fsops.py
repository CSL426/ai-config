"""File operations mirroring the bash helpers (cp -L, rsync -a/-aL semantics)."""

import shutil
from pathlib import Path

from .console import log_warn
from .paths import EXCLUDED_FILES
from .safety import (
    assert_internal_symlinks,
    assert_no_symlinks,
    assert_safe_write_target,
    is_reparse_point,
)


def is_excluded(path: "Path | str") -> bool:
    return Path(path).name in EXCLUDED_FILES


def safe_cp(src: Path, dst: Path) -> None:
    if is_excluded(src):
        log_warn(f"Skipping credential file: {src.name}")
        return
    if is_reparse_point(src):
        raise RuntimeError(f"Refusing reparse point source file: {src}")
    assert_safe_write_target(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_file_to_stage(src: Path, dst: Path) -> None:
    if not src.is_file():
        return
    if is_reparse_point(src):
        raise RuntimeError(f"Refusing reparse point source file: {src}")
    assert_safe_write_target(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def overlay_dir_to_stage(src: Path, dst: Path) -> None:
    """rsync -aL src/ dst/ — merge overlay, dereference symlinks, no deletion."""
    if not src.is_dir():
        return
    assert_no_symlinks(src)
    assert_no_symlinks(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def merge_missing_tree(source: Path, destination: Path, label: str) -> bool:
    assert_no_symlinks(source)
    assert_no_symlinks(destination)
    destination.mkdir(parents=True, exist_ok=True)
    copied = False
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        if any(part in EXCLUDED_FILES for part in relative.parts):
            continue
        target = destination / relative
        conflicting_parent = next(
            (
                destination / parent
                for parent in relative.parents
                if parent != Path(".")
                and (destination / parent).exists()
                and not (destination / parent).is_dir()
            ),
            None,
        )
        if conflicting_parent is not None:
            log_warn(f"Not migrating {label} path conflict: {conflicting_parent}")
            continue
        if item.is_dir():
            if target.exists() and not target.is_dir():
                log_warn(f"Not migrating {label} path conflict: {target}")
                continue
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied = True
    return copied


def mirror_dir(
    src: Path,
    dst: Path,
    *,
    dereference: bool = False,
    exclude_credentials: bool = True,
    allow_internal_symlinks: bool = False,
    exclude_dir_names: "frozenset[str] | None" = None,
) -> None:
    """rsync -a --delete [--exclude creds] src/ dst/ — exact mirror.

    Excluded names are invisible on both sides (not copied, not deleted),
    matching rsync --exclude semantics. `exclude_dir_names` additionally
    skips any path with a directory component matching one of those names
    (e.g. a regenerable cache directory), regardless of nesting depth.
    """
    assert_links = assert_internal_symlinks if allow_internal_symlinks else assert_no_symlinks
    assert_links(src)
    assert_links(dst)
    dst.mkdir(parents=True, exist_ok=True)

    def excluded(p: Path) -> bool:
        if exclude_credentials and is_excluded(p):
            return True
        if exclude_dir_names:
            base = src if p.is_relative_to(src) else dst
            if any(part in exclude_dir_names for part in p.relative_to(base).parts):
                return True
        return False

    src_rels = set()
    for item in sorted(src.rglob("*")):
        if excluded(item):
            continue
        if any(part in EXCLUDED_FILES for part in item.relative_to(src).parts[:-1]) and exclude_credentials:
            continue
        rel = item.relative_to(src)
        src_rels.add(rel)
        target = dst / rel
        if item.is_symlink() and not dereference:
            if target.is_symlink() or target.exists():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(item.readlink())
        elif item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                target.unlink()
            shutil.copy2(item, target)

    protected_directories: set[Path] = set()
    if exclude_credentials:
        for credential in dst.rglob("*"):
            if credential.name not in EXCLUDED_FILES:
                continue
            parent = credential.parent
            while parent != dst:
                protected_directories.add(parent.relative_to(dst))
                parent = parent.parent

    # Deletion pass: anything in dst not present in src (and not excluded)
    for item in sorted(dst.rglob("*"), reverse=True):
        if excluded(item):
            continue
        rel = item.relative_to(dst)
        if rel in protected_directories:
            continue
        if rel not in src_rels:
            if item.is_symlink() or item.is_file():
                item.unlink(missing_ok=True)
            elif item.is_dir():
                shutil.rmtree(item)


def copy_path_dereferenced(src: Path, dst_dir: Path) -> None:
    """rsync -aL <src> <dst_dir>/ — copy file or tree under dst_dir, following links."""
    assert_no_symlinks(src)
    assert_no_symlinks(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    target = dst_dir / src.name
    if src.is_dir():
        shutil.copytree(src, target, symlinks=False, dirs_exist_ok=True)
    elif src.is_file():
        shutil.copy2(src, target)


def dir_has_files(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    return any(p.is_file() for p in directory.rglob("*"))


def count_files(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(
        1
        for path in directory.rglob("*")
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(directory).parts)
    )


def first_existing_file(*candidates: Path) -> "Path | None":
    for path in candidates:
        if path.is_file():
            return path
    return None
