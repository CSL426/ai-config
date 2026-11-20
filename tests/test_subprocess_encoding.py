"""Every text-mode child process says how to decode what it prints."""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "ai_config"
_CALLS = ("run", "Popen", "check_output", "check_call")
_DECODINGS = {"UTF8", "NATIVE"}


def _spreads_a_decoding(node: ast.Call) -> bool:
    """`**UTF8`, `**subproc.NATIVE`, or a choice between the two.

    Any other `**kwargs` would hide whether an encoding was named at all.
    """
    for keyword in node.keywords:
        if keyword.arg is not None:
            continue
        names = {
            part.id if isinstance(part, ast.Name) else part.attr
            for part in ast.walk(keyword.value)
            if isinstance(part, (ast.Name, ast.Attribute))
        }
        if names & _DECODINGS:
            return True
    return False


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
            if not named & {"text", "universal_newlines"}:
                continue
            if _spreads_a_decoding(node) or {"encoding", "errors"} <= named:
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
