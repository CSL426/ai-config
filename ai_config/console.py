"""Console output matching the legacy CLI format."""

import os
import sys


def _configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_configure_utf8_output()

_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ
RED = "\033[0;31m" if _COLOR else ""
GREEN = "\033[0;32m" if _COLOR else ""
YELLOW = "\033[1;33m" if _COLOR else ""
BLUE = "\033[0;34m" if _COLOR else ""
CYAN = "\033[0;36m" if _COLOR else ""
NC = "\033[0m" if _COLOR else ""
BOLD = "\033[1m" if _COLOR else ""


def log_info(msg: str) -> None:
    print(f"{BLUE}ℹ{NC} {msg}")


def log_success(msg: str) -> None:
    print(f"{GREEN}✓{NC} {msg}")


def log_warn(msg: str) -> None:
    print(f"{YELLOW}⚠{NC} {msg}")


def log_error(msg: str) -> None:
    print(f"{RED}✗{NC} {msg}", file=sys.stderr)


def log_header(msg: str) -> None:
    print(f"\n{BOLD}{CYAN}═══ {msg} ═══{NC}")


def ask(prompt: str) -> "str | None":
    """Read one answer, returning None when the user declines to give one.

    Ctrl+C and EOF (a closed stdin, a piped run) are the same intent: leave
    without acting. Returning None instead of raising lets each caller run
    its own cleanup and print its own "cancelled" line, rather than having
    KeyboardInterrupt escape past that and reach the top-level handler.
    """
    try:
        return input(prompt)
    except EOFError:
        return None
    except KeyboardInterrupt:
        # 使用者按 Ctrl+C 時游標停在提示行尾,先換行再讓呼叫端印取消訊息
        print()
        return None


def confirm(prompt: str) -> bool:
    answer = ask(prompt)
    if answer is None:
        return False
    return answer.strip().lower() in {"y", "yes"}
