"""Evaluation comparison primitives for single-agent vs multi-agent runs."""

from .comparison import compare_runs
from .metrics import extract_run_metrics, load_json_if_exists
from .report import render_evaluation_report, write_evaluation_artifacts
from .types import EvaluationComparison

__all__ = [
    "EvaluationComparison",
    "compare_runs",
    "extract_run_metrics",
    "load_json_if_exists",
    "render_evaluation_report",
    "write_evaluation_artifacts",
]
