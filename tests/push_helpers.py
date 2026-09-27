"""Patching the push pipeline, which now spans four modules."""

import pytest

from ai_config import push_preflight, push_publish, push_review
from ai_config.commands import push as push_command

PUSH_MODULES = (push_preflight, push_review, push_publish, push_command)


def patch_push(monkeypatch: pytest.MonkeyPatch, name: str, value: object) -> None:
    """Set `name` on every push module that has it.

    Shared names such as SCRIPT_DIR or _run_repo_git are bound in each
    module separately; patching one would leave the others on the real
    repository. The modules call each other's functions through the
    module, so patching the defining module reaches every caller.
    """
    hits = [module for module in PUSH_MODULES if hasattr(module, name)]
    assert hits, f"no push module defines {name}"
    for module in hits:
        monkeypatch.setattr(module, name, value)
