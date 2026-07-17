"""Shared staging lifecycle for multi-tool apply and project operations."""

import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from .console import log_header
from .fsops import dir_has_files


class ProjectionTool(Protocol):
    def stage_projection(self, destination: Path) -> None: ...


@contextmanager
def staged_projections(
    tools: list[str],
    modules: Mapping[str, ProjectionTool],
    headers: Mapping[str, str],
) -> Iterator[dict[str, Path]]:
    stages: dict[str, Path] = {}
    try:
        for tool in tools:
            log_header(f"Apply {headers[tool]}")
            stage = Path(tempfile.mkdtemp())
            stages[tool] = stage
            try:
                modules[tool].stage_projection(stage)
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to stage {tool} projection: {exc}"
                ) from exc
            if not dir_has_files(stage):
                raise RuntimeError(f"No files staged for {tool}")
        yield stages
    finally:
        for stage in stages.values():
            shutil.rmtree(stage, ignore_errors=True)
