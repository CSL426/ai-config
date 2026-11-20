"""Every text-mode child process says how to decode what it prints."""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "ai_config"
_CALLS = ("run", "Popen", "check_output", "check_call")
_DECODINGS = {"UTF8", "NATIVE"}


def _is_decoding(expr: ast.expr) -> bool:
    """`UTF8`, `subproc.NATIVE`, or a conditional choosing between them."""
    if isinstance(expr, ast.Name):
        return expr.id in _DECODINGS
    if isinstance(expr, ast.Attribute):
        return expr.attr in _DECODINGS
    if isinstance(expr, ast.IfExp):
        return _is_decoding(expr.body) and _is_decoding(expr.orelse)
    return False


def _spreads_a_decoding(node: ast.Call) -> bool:
    """The call spreads one of the named decodings and does not override it.

    Any other `**kwargs` would hide whether an encoding was named at all,
    and a later `encoding=`/`errors=` would quietly replace the one spread.
    """
    named = {k.arg for k in node.keywords if k.arg}
    if named & {"encoding", "errors"}:
        return False
    return any(k.arg is None and _is_decoding(k.value) for k in node.keywords)


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


def test_the_contract_rejects_spreads_that_hide_the_decoding() -> None:
    def call(source: str) -> ast.Call:
        node = ast.parse(source).body[0].value
        assert isinstance(node, ast.Call)
        return node

    assert _spreads_a_decoding(call("run(x, text=True, **UTF8)"))
    assert _spreads_a_decoding(call("run(x, text=True, **(NATIVE if w else subproc.UTF8))"))
    assert not _spreads_a_decoding(call("run(x, text=True, **{**UTF8, 'encoding': None})"))
    assert not _spreads_a_decoding(call("run(x, text=True, **UTF8, encoding='cp950')"))
    assert not _spreads_a_decoding(call("run(x, text=True, **options)"))
