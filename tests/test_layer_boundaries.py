"""The desktop GUI is a layer on top of the CLI, never underneath it."""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "ai_config"
# 只有這兩個入口可以叫起桌面版,而且只經過 desktop
_ENTRY_POINTS = {SOURCE / "cli.py", SOURCE / "cli_dispatch.py"}
_GUI_ONLY = {"webview"}


def _is_gui_module(path: Path) -> bool:
    return path.parent == SOURCE and (path.name.startswith("gui_") or path.name == "desktop.py")


def _imported(tree: ast.AST) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            found.append((node.lineno, base))
            # `from . import gui_api`、`from ai_config import desktop`:從套件
            # 匯入時 names 是子模組;從模組匯入時 names 只是函式,不算
            if base in ("", "ai_config"):
                found.extend((node.lineno, alias.name) for alias in node.names)
    return found


def _leaf(module: str) -> str:
    return module.rpartition(".")[2]


def _violations() -> list[str]:
    found = []
    for path in sorted(SOURCE.rglob("*.py")):
        if _is_gui_module(path):
            continue
        rel = path.relative_to(SOURCE.parent).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno, module in _imported(tree):
            leaf = _leaf(module)
            gui = module.split(".")[0] in _GUI_ONLY or leaf.startswith("gui_")
            if gui or (leaf == "desktop" and path not in _ENTRY_POINTS):
                found.append(f"{rel}:{lineno} imports {module}")
    return found


def test_cli_modules_do_not_depend_on_the_gui_layer() -> None:
    """pywebview is an optional extra, and the CLI must run without it.

    A core module importing gui_* or webview drags the GUI into every
    command, so `acg` breaks on a machine that installed only the CLI.
    The entry points reach the GUI through desktop, and only when asked.
    """
    assert _violations() == []


def test_the_boundary_check_sees_relative_and_absolute_imports() -> None:
    tree = ast.parse(
        "from . import gui_api\n"
        "from .gui_server import serve\n"
        "import ai_config.desktop\n"
        "from ai_config import gui_deploy\n"
        "from webview import create_window\n"
    )
    leaves = [_leaf(module) for _, module in _imported(tree)]
    assert {"gui_api", "gui_server", "desktop", "gui_deploy", "webview"} <= set(leaves)


def test_only_the_real_entry_points_may_reach_desktop() -> None:
    """A nested file that happens to be called cli.py is not an entry point."""
    assert SOURCE / "commands" / "cli.py" not in _ENTRY_POINTS


def test_a_function_named_gui_something_is_not_a_gui_module() -> None:
    tree = ast.parse("from ai_config.desktop import gui_index_path\n")
    assert "gui_index_path" not in [_leaf(module) for _, module in _imported(tree)]
