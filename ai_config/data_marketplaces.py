"""Directory marketplaces inside the data repository, portable across machines.

Claude Code mods live in the data repository (claude/mods), registered as a
`directory` marketplace. Its path names where this machine keeps the
repository, which differs between machines (~/ai-config/data on one,
~/.acg-data on another). Synced as written, it pointed every other machine
at a folder it does not have. The repository therefore stores the path
relative to a placeholder, and apply writes it back as this machine's.
"""

import copy
from pathlib import Path

from . import paths

PLACEHOLDER = "<acg-data>"


def _directory_sources(document: dict):
    markets = document.get("extraKnownMarketplaces")
    if not isinstance(markets, dict):
        return
    for market in markets.values():
        source = market.get("source") if isinstance(market, dict) else None
        if (isinstance(source, dict) and source.get("source") == "directory"
                and isinstance(source.get("path"), str)):
            yield source


def _expand(path: str) -> Path:
    # Windows 上寫出來的路徑可能是反斜線;資料庫裡的路徑不會真的含反斜線
    path = path.replace("\\", "/")
    if path == "~" or path.startswith("~/"):
        return paths.HOME / path[2:]
    return Path(path)


def _inside_repo(path: str) -> "str | None":
    """The path relative to the data repository, with /, or None if outside."""
    repo = Path(paths.SCRIPT_DIR)
    candidate = _expand(path)
    for base, target in ((repo, candidate), (repo.resolve(), candidate.resolve())):
        try:
            relative = target.relative_to(base)
        except ValueError:
            continue
        # 字面上在裡面、靠 .. 跳出去的不算
        if ".." not in relative.parts:
            return relative.as_posix()
    return None


def _legacy(path: str) -> "str | None":
    """A path an older acg stored as the writing machine's own, mapped onto this one.

    Only a folder missing here is remapped, and only onto the longest
    trailing part (two folders or more) this data repository has.
    """
    expanded = _expand(path)
    if expanded.exists():
        return None
    parts = [part for part in expanded.parts if part not in (expanded.anchor, "")]
    repo = Path(paths.SCRIPT_DIR)
    for size in range(len(parts) - 1, 1, -1):
        tail = parts[-size:]
        if ".." not in tail and (repo.joinpath(*tail)).is_dir():
            return "/".join(tail)
    return None


def _local_repo() -> str:
    """This machine's data repository, as Claude Code settings write paths."""
    repo = Path(paths.SCRIPT_DIR)
    try:
        return "~/" + repo.relative_to(paths.HOME).as_posix()
    except ValueError:
        return repo.as_posix()


def portable(document: dict) -> dict:
    """For the repository: a path inside the data repo becomes <acg-data>/..."""
    changes = []
    for source in _directory_sources(document):
        if source["path"].startswith(PLACEHOLDER):
            continue
        relative = _inside_repo(source["path"])
        if relative is not None:
            portable_path = PLACEHOLDER if relative == "." else f"{PLACEHOLDER}/{relative}"
            changes.append((source["path"], portable_path))
    if not changes:
        return document
    result = copy.deepcopy(document)
    replaced = dict(changes)
    for source in _directory_sources(result):
        source["path"] = replaced.get(source["path"], source["path"])
    return result


def local(document: dict) -> dict:
    """For this machine: <acg-data>/... becomes where this machine keeps the repo."""
    repo = _local_repo()
    changes = {}
    for source in _directory_sources(document):
        path = source["path"]
        if path.startswith(PLACEHOLDER):
            changes[path] = repo + path[len(PLACEHOLDER):]
        elif (tail := _legacy(path)) is not None:
            changes[path] = f"{repo}/{tail}"
    if not changes:
        return document
    result = copy.deepcopy(document)
    for source in _directory_sources(result):
        source["path"] = changes.get(source["path"], source["path"])
    return result
