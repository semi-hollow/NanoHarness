"""Evaluation comparison primitives for single-agent vs multi-agent runs."""

from .comparison import compare_runs, compare_variants
from .experiment import compare_benchmark_scorecards, render_ablation_report, write_ablation_comparison
from .feedback_dataset import export_feedback_dataset, record_feedback
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
from .scorecard import (
    build_benchmark_scorecard,
    load_benchmark_scorecard,
    render_benchmark_scorecard,
    write_benchmark_scorecard,
)
from .types import EvaluationComparison

__all__ = [
    "EvaluationComparison",
    "MiniAgentCase",
    "MiniCaseEvaluation",
    "compare_runs",
    "compare_benchmark_scorecards",
    "compare_variants",
    "evaluate_mini_case",
    "extract_run_metrics",
    "load_mini_cases",
    "load_json_if_exists",
    "load_benchmark_scorecard",
    "build_benchmark_scorecard",
    "render_ablation_report",
    "render_benchmark_scorecard",
    "render_evaluation_report",
    "run_mini_cases",
    "write_mini_case_report",
    "write_evaluation_artifacts",
    "write_ablation_comparison",
    "write_benchmark_scorecard",
    "export_feedback_dataset",
    "record_feedback",
]
