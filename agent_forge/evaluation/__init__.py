"""Evaluation comparison primitives for single-agent vs multi-agent runs."""

from .comparison import compare_runs, compare_variants
from .metrics import extract_run_metrics, load_json_if_exists
from .mini_cases import (
    MiniAgentCase,
    MiniCaseEvaluation,
    evaluate_mini_case,
    load_mini_cases,
    run_mini_cases,
    write_mini_case_report,
)
from .report import render_evaluation_report, write_evaluation_artifacts
from .types import EvaluationComparison

__all__ = [
    "EvaluationComparison",
    "MiniAgentCase",
    "MiniCaseEvaluation",
    "compare_runs",
    "compare_variants",
    "evaluate_mini_case",
    "extract_run_metrics",
    "load_mini_cases",
    "load_json_if_exists",
    "render_evaluation_report",
    "run_mini_cases",
    "write_mini_case_report",
    "write_evaluation_artifacts",
]
