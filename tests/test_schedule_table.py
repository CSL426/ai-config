"""三台機器都排四點就會同時醒來;時間表讓它們各自有一分鐘。"""

from pathlib import Path

import pytest

from ai_config import schedule_table as table


@pytest.fixture
def notebook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "memory"
    root.mkdir(parents=True)
    monkeypatch.setattr(table, "memory_dir", lambda: root)
    return root


def test_an_absent_table_is_empty_not_an_error(notebook: Path) -> None:
    loaded = table.load()

    assert loaded.hosts == {}
    assert loaded.hour == table.DEFAULT_HOUR


def test_a_corrupt_file_only_hides_that_one_machine(notebook: Path) -> None:
    table.record("good", table.Slot(4, 0))
    table.table_dir().joinpath("bad.toml").write_text("這不是 TOML [[[")

    assert list(table.load().hosts) == ["good"]


def test_machines_are_spaced_apart(notebook: Path) -> None:
    current = table.load()
    first = table.claim(current, "one")
    current.hosts["one"] = first
    second = table.claim(current, "two")
    current.hosts["two"] = second
    third = table.claim(current, "three")

    assert [str(first), str(second), str(third)] == ["04:00", "04:10", "04:20"]


def test_a_machine_keeps_the_slot_it_already_has(notebook: Path) -> None:
    table.record("one", table.Slot(4, 30))

    assert str(table.claim(table.load(), "one")) == "04:30"


def test_slots_wrap_inside_the_hour(notebook: Path) -> None:
    # 排到隔壁小時比共用一分鐘更糟:那是沒有人選過的時間
    current = table.load()
    for index in range(6):
        current.hosts[f"host{index}"] = table.Slot(4, index * 10)

    assert table.claim(current, "seventh").hour == 4


def test_a_recorded_slot_survives_a_round_trip(notebook: Path) -> None:
    table.record("gpu-a4000", table.Slot(4, 20))

    assert str(table.load().hosts["gpu-a4000"]) == "04:20"


def test_recording_the_same_slot_changes_nothing(notebook: Path) -> None:
    table.record("one", table.Slot(4, 0))

    assert table.record("one", table.Slot(4, 0)) is False


def test_a_hand_written_file_is_respected(notebook: Path) -> None:
    table.table_dir().mkdir(parents=True, exist_ok=True)
    table.host_path("mine").write_text(
        'host = "mine"\nslot = "02:15"\ndefault_hour = 2\nspacing_minutes = 5\n',
        encoding="utf-8",
    )

    loaded = table.load()

    assert loaded.hour == 2
    assert loaded.spacing == 5
    assert str(loaded.hosts["mine"]) == "02:15"


def test_an_impossible_time_is_ignored(notebook: Path) -> None:
    table.table_dir().mkdir(parents=True, exist_ok=True)
    table.host_path("bad").write_text('host = "bad"\nslot = "99:99"\n')
    table.host_path("good").write_text('host = "good"\nslot = "04:00"\n')

    assert list(table.load().hosts) == ["good"]


def test_the_host_name_is_short_and_never_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(table.socket, "gethostname", lambda: "box.example.com")
    assert table.host_name() == "box"

    monkeypatch.setattr(table.socket, "gethostname", lambda: "")
    assert table.host_name() == "unknown-host"


def test_the_later_name_gives_way_when_two_pick_the_same_minute(
    notebook: Path,
) -> None:
    """兩台同時 enable 都會挑到同一分鐘,同步後才看得見彼此。"""
    current = table.Table(4, 10, {
        "machine-a": table.Slot(4, 10),
        "machine-b": table.Slot(4, 10),
    })

    assert table.resolve_collision(current, "machine-a") is None
    assert str(table.resolve_collision(current, "machine-b")) == "04:00"


def test_nobody_moves_when_there_is_no_collision(notebook: Path) -> None:
    current = table.Table(4, 10, {
        "machine-a": table.Slot(4, 0),
        "machine-b": table.Slot(4, 10),
    })

    assert table.resolve_collision(current, "machine-a") is None
    assert table.resolve_collision(current, "machine-b") is None


def test_both_sides_reach_the_same_verdict(notebook: Path) -> None:
    # 兩台各自計算,不通訊,結論必須一致,否則會一起讓位或一起不讓
    current = table.Table(4, 10, {
        "zulu": table.Slot(4, 20),
        "alpha": table.Slot(4, 20),
    })

    stays = table.resolve_collision(current, "alpha")
    moves = table.resolve_collision(current, "zulu")

    assert stays is None and moves is not None


def test_a_full_hour_leaves_the_collision_alone(notebook: Path) -> None:
    # 沒有空位時共用一分鐘,好過排到沒人選過的小時
    hosts = {f"host{i}": table.Slot(4, i * 10) for i in range(6)}
    hosts["late"] = table.Slot(4, 0)
    current = table.Table(4, 10, hosts)

    assert table.resolve_collision(current, "late") is None


def test_each_machine_writes_its_own_file(notebook: Path) -> None:
    """共用一個檔時 git 得挑一個贏家,實際上兩邊都只剩自己。"""
    table.record("alpha", table.Slot(4, 0))
    table.record("bravo", table.Slot(4, 10))

    names = sorted(p.name for p in table.table_dir().glob("*.toml"))

    assert names == ["alpha.toml", "bravo.toml"]
    assert sorted(table.load().hosts) == ["alpha", "bravo"]


def test_a_hostile_host_name_cannot_escape_the_directory(notebook: Path) -> None:
    path = table.host_path("../../etc/passwd")

    assert path.parent == table.table_dir()
