"""The release build writes secrets into source constants; the targets must exist."""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/standalone-release.yml"
# sed -i.bak 's/^NAME = ""/NAME = "'"$VALUE"'"/' ai_config/module.py
_INJECTION = re.compile(r"""sed -i\S* 's/\^(\w+) = ""/.*?' (\S+\.py)""")


def _injections() -> list[tuple[str, str]]:
    return _INJECTION.findall(WORKFLOW.read_text(encoding="utf-8"))


def test_the_release_injects_something() -> None:
    names = {name for name, _ in _injections()}
    assert {"GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET"} <= names


@pytest.mark.parametrize(("name", "target"), _injections())
def test_each_injected_constant_is_where_the_release_writes_it(
    name: str, target: str,
) -> None:
    # 拆模組時常數搬走、sed 還指著舊檔,正式版就少了 client ID;
    # workflow 雖然接著 grep -q 會讓建置失敗,但那要等到發版才知道
    path = REPO_ROOT / target
    assert path.is_file(), f"{target} no longer exists"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith(f'{name} = ""') for line in lines), (
        f'{target} has no line starting with {name} = ""'
    )
