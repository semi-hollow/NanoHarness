from __future__ import annotations

from dataclasses import dataclass
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
from agent_forge.evaluation.domain.ablation import (
    AblationComparisonRequest,
    compare_benchmark_scorecards,
)
from agent_forge.evaluation.domain.comparison import compare_runs, compare_variants
from agent_forge.evaluation.domain.models import EvaluationComparison
from agent_forge.evaluation.domain.run_metrics import extract_run_metrics
from agent_forge.evaluation.presentation.ablation_report import render_ablation_report
from agent_forge.evaluation.presentation.comparison_report import (
    render_evaluation_report,
)
from agent_forge.evaluation.presentation.scorecard_report import (
    render_benchmark_scorecard,
)
from agent_forge.evaluation.wiring import build_scorecard_use_case


# 核心数据：读取两组 benchmark 并发布 ablation artifact 的请求。
@dataclass(frozen=True)
class AblationArtifactRequest:
    """Control/treatment 目录、唯一变量、输出目录和展示标签。"""

    control_dir: str | Path
    treatment_dir: str | Path
    factor: str
    output_dir: str | Path
    control_label: str = "control"
    treatment_label: str = "treatment"


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


def write_ablation_comparison(
    request: AblationArtifactRequest,
) -> tuple[Path, Path]:
    comparison = compare_benchmark_scorecards(
        AblationComparisonRequest(
            control=load_benchmark_scorecard(request.control_dir),
            treatment=load_benchmark_scorecard(request.treatment_dir),
            factor=request.factor,
            control_label=request.control_label,
            treatment_label=request.treatment_label,
        )
    )
    output = Path(request.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "ablation.json"
    report_path = output / "ablation.md"
    write_json_object(json_path, comparison)
    report_path.write_text(render_ablation_report(comparison), encoding="utf-8")
    return json_path, report_path


__all__ = [
    "AblationArtifactRequest",
    "AblationComparisonRequest",
    "EvaluationComparison",
    "FeedbackRequest",
    "ImprovementRecordRequest",
    "build_benchmark_scorecard",
    "compare_benchmark_scorecards",
    "compare_runs",
    "compare_variants",
    "export_feedback_dataset",
    "extract_run_metrics",
    "load_benchmark_scorecard",
    "load_json_if_exists",
    "record_feedback",
    "render_ablation_report",
    "render_benchmark_scorecard",
    "render_evaluation_report",
    "write_ablation_comparison",
    "write_benchmark_scorecard",
    "write_evaluation_artifacts",
    "write_improvement_record",
]
