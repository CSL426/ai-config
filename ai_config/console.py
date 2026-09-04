"""Console output matching the legacy CLI format."""

import os
import sys
import time


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


INTERRUPT_WINDOW_SECONDS = 2.0


def ask(prompt: str) -> "str | None":
    """Read one answer, returning None when the user declines to give one.

    A single Ctrl+C is treated as a slip: say so and re-read, the way an
    interactive shell does. Two within INTERRUPT_WINDOW_SECONDS is a
    deliberate quit. EOF (closed stdin, a piped run) means there is nobody
    to ask, so it gives up at once.

    Returning None rather than raising lets each caller run its own cleanup
    and print its own "cancelled" line, instead of KeyboardInterrupt
    escaping past that to the top-level handler.
    """
    # None,不是 0.0:monotonic() 的原點未定義,用數值當哨兵會讓第一次中斷
    # 在時鐘剛好接近 0 時被誤判成「連續兩次」
    last_interrupt: float | None = None
    while True:
        try:
            return input(prompt)
        except EOFError:
            return None
        except KeyboardInterrupt:
            now = time.monotonic()
            # 游標停在提示行尾,先換行再印訊息
            print()
            if (
                last_interrupt is not None
                and now - last_interrupt <= INTERRUPT_WINDOW_SECONDS
            ):
                return None
            last_interrupt = now
            print("再按一次 Ctrl+C 取消", file=sys.stderr)


def confirm(prompt: str) -> bool:
    answer = ask(prompt)
    if answer is None:
        return False
    return answer.strip().lower() in {"y", "yes"}
