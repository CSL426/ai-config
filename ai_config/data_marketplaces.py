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
            return target.relative_to(base).as_posix()
        except ValueError:
            continue
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
    if not any(s["path"].startswith(PLACEHOLDER) for s in _directory_sources(document)):
        return document
    result = copy.deepcopy(document)
    repo = _local_repo()
    for source in _directory_sources(result):
        if source["path"].startswith(PLACEHOLDER):
            source["path"] = repo + source["path"][len(PLACEHOLDER):]
    return result
