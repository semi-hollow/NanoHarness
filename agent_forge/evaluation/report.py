"""Compatibility exports for evaluation reports."""

from .api import write_evaluation_artifacts
from .presentation.comparison_report import render_evaluation_report

__all__ = ["render_evaluation_report", "write_evaluation_artifacts"]
