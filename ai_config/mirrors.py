"""Shared-skill mirror drift detection (metadata.mirror-of / mirror-hash)."""

import hashlib
import re
from pathlib import Path

from .console import CYAN, NC, log_success, log_warn
from .paths import HOME, SCRIPT_DIR, tilde


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _frontmatter_value(skill_md: Path, key: str) -> str:
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        return ""
    in_metadata = False
    pattern = re.compile(rf"^[ \t]+{re.escape(key)}:[ \t]*(.*)$")
    for line in lines[1:]:
        if line == "---":
            break
        if re.match(r"^metadata:\s*$", line):
            in_metadata = True
            continue
        if in_metadata and line and not line[0].isspace():
            in_metadata = False
        if in_metadata and (match := pattern.match(line)):
            return _yaml_scalar(match.group(1))
    return ""


def check_shared_mirrors() -> None:
    shared_root = SCRIPT_DIR / "claude" / "shared"
    if not shared_root.is_dir():
        return

    checked = 0
    stale = 0

    for skill_md in sorted(shared_root.glob("*/*/SKILL.md")):
        src_text = _frontmatter_value(skill_md, "mirror-of")
        if not src_text:
            continue
        src_hash = _frontmatter_value(skill_md, "mirror-hash")

        checked += 1
        rel = skill_md.relative_to(shared_root)
        rel_text = rel.as_posix()
        src_path = Path(
            src_text.replace("~", str(HOME), 1)
            if src_text.startswith("~")
            else src_text
        )

        if not src_path.is_file():
            log_warn(f"mirror source missing: {rel_text} ← {tilde(src_path)}")
            stale += 1
            continue

        cur_hash = hashlib.sha256(src_path.read_bytes()).hexdigest()
        if cur_hash.lower() != src_hash.lower():
            log_warn(
                f"mirror stale: {rel_text} — source changed: {tilde(src_path)}"
            )
            print(f"    update the copy, then set {CYAN}mirror-hash: {cur_hash}{NC}")
            stale += 1

    if checked > 0 and stale == 0:
        log_success(f"All {checked} mirrored shared skills up to date")
