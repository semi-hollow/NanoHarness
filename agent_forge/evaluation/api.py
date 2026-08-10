from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_forge.evaluation.adapters.feedback_dataset_files import (
    FeedbackRequest,
    ImprovementRecordRequest,
    export_feedback_dataset,
    record_feedback,
    write_improvement_record,
)
from agent_forge.evaluation.adapters.json_files import (
    load_json_if_exists,
    read_json_object,
    write_json_object,
)
from agent_forge.evaluation.domain.comparison import compare_runs
from agent_forge.evaluation.domain.models import EvaluationComparison
from agent_forge.evaluation.domain.run_metrics import extract_run_metrics
from agent_forge.evaluation.presentation.comparison_report import (
    render_evaluation_report,
)
from agent_forge.evaluation.presentation.scorecard_report import (
    render_benchmark_scorecard,
)
from agent_forge.evaluation.wiring import build_scorecard_use_case


# 主要入口：从 benchmark 运行事实与 artifact 构造稳定定量 scorecard。
def build_benchmark_scorecard(
    results: dict[str, Any],
    run_dir: str | Path,
) -> dict[str, Any]:
    """通过正式用例构造一次 benchmark 的稳定 scorecard。"""

    return build_scorecard_use_case().build_scorecard(results, run_dir)


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
        raise ValueError(f"benchmark run has no scorecard.json or results.json: {root}")
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


__all__ = [
    "EvaluationComparison",
    "FeedbackRequest",
    "ImprovementRecordRequest",
    "build_benchmark_scorecard",
    "compare_runs",
    "export_feedback_dataset",
    "extract_run_metrics",
    "load_benchmark_scorecard",
    "load_json_if_exists",
    "record_feedback",
    "render_benchmark_scorecard",
    "render_evaluation_report",
    "write_benchmark_scorecard",
    "write_evaluation_artifacts",
    "write_improvement_record",
]
