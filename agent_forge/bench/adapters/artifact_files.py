from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_forge.bench.adapters.case_evidence import JsonCaseEvidenceReader
from agent_forge.bench.application.failure_analysis import BenchFailureAnalyzer
from agent_forge.bench.domain.config import BenchRunLayout
from agent_forge.bench.domain.models import BenchCaseResult, BenchRunSummary
from agent_forge.bench.ports.benchmark import BenchArtifactPort
from agent_forge.bench.presentation.case_study import write_case_study
from agent_forge.bench.presentation.report import write_bench_artifacts
from agent_forge.evaluation.api import (
    EvaluationComparison,
    load_json_if_exists,
    write_evaluation_artifacts,
)


class FileBenchArtifacts(BenchArtifactPort):
    """文件系统产物 Adapter；显式继承 Port 以便 IDE 直接跳转实现。"""

    def __init__(self) -> None:
        self._failure_analyzer = BenchFailureAnalyzer(JsonCaseEvidenceReader())

    def create_layout(
        self,
        output_root: str,
        run_id: str,
        *,
        include_baseline: bool,
    ) -> BenchRunLayout:
        output_dir = (Path(output_root) / run_id).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        return BenchRunLayout(
            output_dir=output_dir,
            predictions_path=output_dir / "predictions.jsonl",
            baseline_predictions_path=(
                output_dir / "direct_baseline_predictions.jsonl"
                if include_baseline
                else None
            ),
        )

    def read_json(self, path: Path) -> dict[str, Any]:
        return load_json_if_exists(path)

    @staticmethod
    def prediction_for(
        result: BenchCaseResult,
        *,
        provider: str,
        model: str | None,
    ) -> dict[str, Any]:
        """映射成 SWE-bench 规定的 prediction schema。

        ``model_patch`` 是外部协议字段；内部文件明确叫
        ``candidate_changes.diff``，两者不要混作 Runtime 工具名。
        """

        return {
            "instance_id": result.instance_id,
            "model_name_or_path": f"agent-forge-{provider}-{model or 'default'}",
            "model_patch": (
                result.candidate_diff_path.read_text(encoding="utf-8")
                if result.candidate_diff_path.exists()
                else ""
            ),
        }

    def write_comparison(
        self,
        comparison: EvaluationComparison,
        output_dir: Path,
    ) -> None:
        write_evaluation_artifacts(comparison, output_dir)

    @staticmethod
    def copy_candidate_diff(source: Path, destination: Path) -> None:
        """复制候选 unified diff 到另一个 variant 的证据目录。"""

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            source.read_text(encoding="utf-8") if source.exists() else "",
            encoding="utf-8",
        )

    def finalize_case(self, result: BenchCaseResult) -> None:
        """最终评测结束后归因，并基于同一最终结果写 Case Study。"""

        self._failure_analyzer.enrich_result_with_failure_diagnosis(result)
        write_case_study(result)

    def publish_run(
        self,
        summary: BenchRunSummary,
        predictions: list[dict[str, Any]],
        baseline_predictions: list[dict[str, Any]],
    ) -> None:
        self.write_predictions(summary, predictions, baseline_predictions)
        write_bench_artifacts(summary)
        latest = Path(".agent_forge/latest")
        latest.mkdir(parents=True, exist_ok=True)
        (latest / "bench.txt").write_text(
            str(summary.output_dir),
            encoding="utf-8",
        )

    def write_predictions(
        self,
        summary: BenchRunSummary,
        predictions: list[dict[str, Any]],
        baseline_predictions: list[dict[str, Any]],
    ) -> None:
        _write_jsonl(summary.predictions_path, predictions)
        if summary.baseline_predictions_path is not None:
            _write_jsonl(summary.baseline_predictions_path, baseline_predictions)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
