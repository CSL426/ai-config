"""Console output matching the legacy CLI format."""

import os
import sys

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
