"""Apply categories classify destination management units."""

from collections.abc import Iterable

CATEGORIES = ("settings", "skills", "all")


def validate_category(category: str) -> str:
    if category not in CATEGORIES:
        raise ValueError(f"Unknown apply category: {category}")
    return category


def includes(category: str, selected: str) -> bool:
    validate_category(category)
    return category in ("all", selected)


def selected_paths(names: Iterable[str], category: str) -> list[str]:
    return [
        name for name in names
        if includes(category, "skills" if name == "skills" else "settings")
    ]
