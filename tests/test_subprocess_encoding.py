"""Every text-mode child process says how to decode what it prints."""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "ai_config"
_CALLS = ("run", "Popen", "check_output", "check_call")


def _undeclared() -> list[str]:
    found = []
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in _CALLS:
                continue
            named = {k.arg for k in node.keywords if k.arg}
            spread = any(k.arg is None for k in node.keywords)
            if not named & {"text", "universal_newlines"}:
                continue
            if spread or {"encoding", "errors"} <= named:
                continue
            found.append(f"{path.relative_to(SOURCE.parent)}:{node.lineno}")
    return found


def test_no_child_output_is_decoded_with_the_locale_default() -> None:
    """text=True alone decodes with the locale codepage, cp950 on the Windows box.

    A child printing UTF-8 Chinese then raised UnicodeDecodeError inside
    subprocess's reader thread during `apply`. The main flow still exited
    0, so the failure only showed up as a traceback nobody was reading.
    Every call now names its encoding and replaces what it cannot decode.
    """
    assert _undeclared() == []
