"""Make the in-repo ai_config package importable regardless of whether (or
how) ai-config is installed — tests must not depend on a pip/pipx install."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
