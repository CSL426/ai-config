"""Helpers shared by the setup/pull and release-contract tests."""

import tomllib

from data_repo_helpers import (
    REPO_ROOT,
)


def project_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)["project"]["version"]
