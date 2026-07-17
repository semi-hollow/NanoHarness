from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_forge.evaluation.adapters.feedback_dataset_files import (
    export_feedback_dataset,
    record_feedback,
)
from agent_forge.evaluation.adapters.json_files import (
    load_json_if_exists,
    read_json_object,
    write_json_object,
)
from agent_forge.evaluation.adapters.mini_case_files import (
    load_mini_cases,
    write_mini_case_artifacts,
)
from agent_forge.evaluation.application.mini_cases import evaluate_selected_cases
from agent_forge.evaluation.domain.ablation import compare_benchmark_scorecards
from agent_forge.evaluation.domain.comparison import compare_runs, compare_variants
from agent_forge.evaluation.domain.mini_cases import (
    MiniAgentCase,
    MiniCaseEvaluation,
    evaluate_mini_case,
)
from agent_forge.evaluation.domain.models import EvaluationComparison
from agent_forge.evaluation.domain.run_metrics import extract_run_metrics
from agent_forge.evaluation.presentation.ablation_report import render_ablation_report
from agent_forge.evaluation.presentation.comparison_report import render_evaluation_report
from agent_forge.evaluation.presentation.mini_case_report import render_mini_case_report
from agent_forge.evaluation.presentation.scorecard_report import render_benchmark_scorecard
from agent_forge.evaluation.wiring import build_scorecard_use_case

# 主要入口：从 benchmark 运行事实与 artifact 构造稳定定量 scorecard。
def build_benchmark_scorecard(
    results: dict[str, Any],
    run_dir: str | Path,
) -> dict[str, Any]:
    """通过正式用例构造一次 benchmark 的稳定 scorecard。"""

    return build_scorecard_use_case().execute(results, run_dir)


def write_benchmark_scorecard(
    results: dict[str, Any],
    run_dir: str | Path,
) -> tuple[Path, Path]:
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    scorecard = build_benchmark_scorecard(results, root)
    json_path = root / "scorecard.json"
    report_path = root / "scorecard.md"
    write_json_object(json_path, scorecard)
    report_path.write_text(render_benchmark_scorecard(scorecard), encoding="utf-8")
    return json_path, report_path


def load_benchmark_scorecard(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir)
    scorecard_path = root / "scorecard.json"
    if scorecard_path.exists():
        return read_json_object(scorecard_path)
    results_path = root / "results.json"
    if not results_path.exists():
        raise ValueError(
            f"benchmark run has no scorecard.json or results.json: {root}"
        )
    return build_benchmark_scorecard(read_json_object(results_path), root)


def write_evaluation_artifacts(
    comparison: EvaluationComparison,
    output_dir: str | Path,
) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "comparison.json"
    report_path = output / "evaluation_report.md"
    write_json_object(json_path, comparison.to_dict())
    report_path.write_text(render_evaluation_report(comparison), encoding="utf-8")
    return json_path, report_path


def write_ablation_comparison(
    control_dir: str | Path,
    treatment_dir: str | Path,
    *,
    factor: str,
    output_dir: str | Path,
    control_label: str = "control",
    treatment_label: str = "treatment",
) -> tuple[Path, Path]:
    comparison = compare_benchmark_scorecards(
        load_benchmark_scorecard(control_dir),
        load_benchmark_scorecard(treatment_dir),
        factor=factor,
        control_label=control_label,
        treatment_label=treatment_label,
    )
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "ablation.json"
    report_path = output / "ablation.md"
    write_json_object(json_path, comparison)
    report_path.write_text(render_ablation_report(comparison), encoding="utf-8")
    return json_path, report_path


def write_mini_case_report(
    case: MiniAgentCase,
    result: MiniCaseEvaluation,
    output_dir: str | Path,
) -> Path:
    return write_mini_case_artifacts(
        case,
        result,
        output_dir,
        render_mini_case_report(case, result),
    )


def run_mini_cases(
    *,
    case_id: str = "all",
    evidence: dict[str, Any] | None = None,
    output_dir: str | Path = ".agent_forge/mini_cases",
    case_root: str | Path | None = None,
) -> list[Path]:
    evaluated = evaluate_selected_cases(
        load_mini_cases(case_root),
        case_id=case_id,
        evidence=evidence,
    )
    return [
        write_mini_case_report(case, result, output_dir)
        for case, result in evaluated
    ]

__all__ = [
    "EvaluationComparison",
    "MiniAgentCase",
    "MiniCaseEvaluation",
    "build_benchmark_scorecard",
    "compare_benchmark_scorecards",
    "compare_runs",
    "compare_variants",
    "evaluate_mini_case",
    "export_feedback_dataset",
    "extract_run_metrics",
    "load_benchmark_scorecard",
    "load_json_if_exists",
    "load_mini_cases",
    "record_feedback",
    "render_ablation_report",
    "render_benchmark_scorecard",
    "render_evaluation_report",
    "run_mini_cases",
    "write_ablation_comparison",
    "write_benchmark_scorecard",
    "write_evaluation_artifacts",
    "write_mini_case_report",
]
