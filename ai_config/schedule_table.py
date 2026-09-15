"""Which machine saves memory at which minute, agreed through the notebook.

Every machine defaults to the same hour, so they wake together and race to
push. This hands each one its own minute: a machine claims the next free
slot and writes it where the others will read it, riding on the notebook's
own sync with no server involved.

One file per machine, not one shared table. Two machines editing a shared
file forces git to pick a winner, and it picked badly in practice — each
side ended up with a table naming only itself. Separate files merge with
no decision to make.
"""

import socket
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .memory import memory_dir

TABLE_DIR = "autopush-schedule"
DEFAULT_HOUR = 4
DEFAULT_SPACING = 10
_MAX_SLOTS = 6


def table_dir() -> Path:
    return memory_dir() / TABLE_DIR


def host_path(host: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in host)
    return table_dir() / f"{safe.strip('-.') or 'unknown-host'}.toml"


def host_name() -> str:
    """This machine's name, as the others will know it."""
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
    """Every machine's claim. A broken file is one machine missing, not an error."""
    hour, spacing, hosts = DEFAULT_HOUR, DEFAULT_SPACING, {}
    root = table_dir()
    if not root.is_dir():
        return Table(hour, spacing, hosts)
    for path in sorted(root.glob("*.toml")):
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(raw.get("default_hour"), int):
            hour = max(0, min(23, raw["default_hour"]))
        if isinstance(raw.get("spacing_minutes"), int):
            spacing = max(1, min(30, raw["spacing_minutes"]))
        name = raw.get("host")
        slot = _parse_slot(raw.get("slot"))
        if isinstance(name, str) and name and slot is not None:
            hosts[name] = slot
    return Table(hour, spacing, hosts)


def _free_slot(table: Table) -> "Slot | None":
    taken = {(s.hour, s.minute) for s in table.hosts.values()}
    for index in range(_MAX_SLOTS):
        minute = (index * table.spacing) % 60
        if (table.hour, minute) not in taken:
            return Slot(table.hour, minute)
    return None


def claim(table: Table, host: str) -> Slot:
    """This host's slot, taking the next free one when it has none.

    Slots wrap inside the hour rather than spilling into the next: sharing
    a minute is better than running at an hour nobody chose.
    """
    existing = table.hosts.get(host)
    if existing is not None:
        return existing
    return _free_slot(table) or Slot(table.hour, 0)


def resolve_collision(table: Table, host: str) -> "Slot | None":
    """A new slot when somebody else holds this one, else None.

    Two machines claiming before either has synced both read the same
    table and pick the same minute. Neither can see the other until the
    notebook syncs, so the tie is broken here: the name that sorts later
    moves on, which both sides compute identically without talking.
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
    return _free_slot(table)


def render(host: str, slot: Slot, table: Table) -> str:
    return (
        "# 這台機器保存記憶的時間。一台一個檔,幾台同時改也不會互相覆蓋。\n"
        "# 可以手動改 slot,那台下次跑排程時會自己跟上。\n"
        "\n"
        f'host = "{host}"\n'
        f'slot = "{slot}"\n'
        f"default_hour = {table.hour}\n"
        f"spacing_minutes = {table.spacing}\n"
    )


def record(host: str, slot: Slot) -> bool:
    """Write this host's claim. False when it already said the same thing."""
    table = load()
    if table.hosts.get(host) == slot:
        return False
    path = host_path(host)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(host, slot, table), encoding="utf-8")
    return True
