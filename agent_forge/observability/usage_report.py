"""Compatibility exports for usage read-model generation."""

from .adapters.usage_files import usage_artifact_paths
from .api import write_usage_artifacts
from .domain.usage import build_usage_report
from .presentation.usage_report import render_usage_markdown

__all__ = [
    "build_usage_report",
    "render_usage_markdown",
    "usage_artifact_paths",
    "write_usage_artifacts",
]
