"""Which machine saves memory at which minute, agreed through the notebook.

Every machine defaults to the same hour, so they wake together and race to
push. The table hands each one its own slot: a machine claims the next free
minute, writes it where the others will read it, and keeps that slot for
good. It rides on the notebook's own sync, so no server is involved.

TOML because it is the only structured format Python reads without a
dependency the frozen executable would have to carry, and because a person
editing this by hand wants comments.
"""

import socket
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .memory import memory_dir

TABLE_NAME = "autopush-schedule.toml"
DEFAULT_HOUR = 4
DEFAULT_SPACING = 10
_MAX_SLOTS = 6


def table_path() -> Path:
    return memory_dir() / TABLE_NAME


def host_name() -> str:
    """This machine's name, as the table will know it."""
    try:
        name = socket.gethostname().strip()
    except OSError:
        name = ""
    return name.split(".")[0] or "unknown-host"


@dataclass
class Slot:
    hour: int
    minute: int

    def __str__(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


@dataclass
class Table:
    hour: int
    spacing: int
    hosts: dict[str, Slot]


def _parse_slot(value: object) -> "Slot | None":
    if not isinstance(value, str) or ":" not in value:
        return None
    hour, _, minute = value.partition(":")
    try:
        slot = Slot(int(hour), int(minute))
    except ValueError:
        return None
    if not (0 <= slot.hour <= 23 and 0 <= slot.minute <= 59):
        return None
    return slot


def load() -> Table:
    """Read the table. A missing or broken file is an empty one, not an error."""
    path = table_path()
    hour, spacing, hosts = DEFAULT_HOUR, DEFAULT_SPACING, {}
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Table(hour, spacing, hosts)
    if isinstance(raw.get("default_hour"), int):
        hour = max(0, min(23, raw["default_hour"]))
    if isinstance(raw.get("spacing_minutes"), int):
        spacing = max(1, min(30, raw["spacing_minutes"]))
    for name, value in (raw.get("hosts") or {}).items():
        slot = _parse_slot(value)
        if slot is not None:
            hosts[str(name)] = slot
    return Table(hour, spacing, hosts)


def claim(table: Table, host: str) -> Slot:
    """This host's slot, taking the next free one when it has none.

    Slots wrap within the hour rather than spilling into the next, so a
    late machine shares a minute rather than running at an hour nobody
    chose. Six machines at ten minutes apart fills the hour.
    """
    existing = table.hosts.get(host)
    if existing is not None:
        return existing
    taken = {(s.hour, s.minute) for s in table.hosts.values()}
    for index in range(_MAX_SLOTS):
        minute = (index * table.spacing) % 60
        if (table.hour, minute) not in taken:
            return Slot(table.hour, minute)
    return Slot(table.hour, 0)


def render(table: Table) -> str:
    lines = [
        "# 每台機器保存記憶的時間。acg memory autopush enable 會自己認領一個",
        "# 空的時段並寫進來,也可以手動改;改完在那台重新 enable 才會生效。",
        "",
        f"default_hour = {table.hour}",
        f"spacing_minutes = {table.spacing}",
        "",
        "[hosts]",
    ]
    for name in sorted(table.hosts):
        lines.append(f'"{name}" = "{table.hosts[name]}"')
    return "\n".join(lines) + "\n"


def resolve_collision(table: Table, host: str) -> "Slot | None":
    """A new slot when somebody else holds this one, else None.

    Two machines enabling before either has synced both read the same
    table and pick the same free minute. Neither can see the other until
    the notebook syncs, so the tie is broken here instead: the host whose
    name sorts later moves on, which both sides compute the same way
    without talking to each other.
    """
    mine = table.hosts.get(host)
    if mine is None:
        return None
    rivals = [
        name
        for name, slot in table.hosts.items()
        if name != host and (slot.hour, slot.minute) == (mine.hour, mine.minute)
    ]
    if not rivals or host < min(rivals):
        return None
    taken = {(s.hour, s.minute) for s in table.hosts.values()}
    for index in range(_MAX_SLOTS):
        minute = (index * table.spacing) % 60
        if (table.hour, minute) not in taken:
            return Slot(table.hour, minute)
    return None


def record(host: str, slot: Slot) -> bool:
    """Write this host's slot into the table. False when it was already there."""
    table = load()
    if table.hosts.get(host) == slot:
        return False
    table.hosts[host] = slot
    path = table_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(table), encoding="utf-8")
    return True
