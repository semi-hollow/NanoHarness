"""Evaluation comparison primitives for single-agent vs multi-agent runs."""

from .comparison import compare_runs, compare_variants
from .metrics import extract_run_metrics, load_json_if_exists
from .report import render_evaluation_report, write_evaluation_artifacts
from .types import EvaluationComparison

__all__ = [
    "EvaluationComparison",
    "compare_runs",
    "compare_variants",
    "extract_run_metrics",
    "load_json_if_exists",
    "render_evaluation_report",
    "write_evaluation_artifacts",
]
