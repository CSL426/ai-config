"""Machine-local settings.json filtering shared by tool sync modules.

Fields like permission allowlists and trusted-workspace paths grow per
machine and would churn (and leak workflow detail) if committed, so init
strips them, apply preserves the live values, and status ignores them.
"""

import json
import os
from pathlib import Path

from .safety import assert_safe_write_target, is_reparse_point


def parse_settings(text: str, label: str) -> dict[str, object]:
    document = json.loads(text.lstrip("\ufeff"))
    if not isinstance(document, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return document


def format_settings(document: dict[str, object]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def filter_settings(text: str, keys: frozenset[str], label: str) -> str:
    document = parse_settings(text, label)
    if not keys.intersection(document):
        return text.lstrip("\ufeff")
    filtered = {
        key: value for key, value in document.items() if key not in keys
    }
    return format_settings(filtered)


def merge_settings(
    source_text: str, target_text: str, keys: frozenset[str], label: str
) -> str:
    filtered_source = filter_settings(source_text, keys, label)
    source = parse_settings(filtered_source, label)
    target = parse_settings(target_text, label)
    if not keys.intersection(target):
        return filtered_source
    for key in sorted(keys):
        if key in target:
            source[key] = target[key]
    return format_settings(source)


def shared_settings(
    text: str, keys: frozenset[str], label: str
) -> dict[str, object]:
    document = parse_settings(text, label)
    return {key: value for key, value in document.items() if key not in keys}


def read_settings(path: Path) -> str:
    if is_reparse_point(path):
        raise RuntimeError(f"Refusing reparse point source file: {path}")
    return path.read_text(encoding="utf-8-sig")


def write_settings(source: Path, destination: Path, content: str) -> None:
    if is_reparse_point(source):
        raise RuntimeError(f"Refusing reparse point source file: {source}")
    assert_safe_write_target(destination)
    source_stat = source.stat()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8", newline="\n")
    os.utime(
        destination,
        ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
    )
