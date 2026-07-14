from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_forge.bench.adapters.case_evidence import JsonCaseEvidenceReader
from agent_forge.bench.application.diagnostics import DiagnoseBenchCase
from agent_forge.bench.domain.config import BenchRunLayout
from agent_forge.bench.domain.models import BenchCaseResult, BenchRunSummary
from agent_forge.bench.presentation.case_study import write_case_study
from agent_forge.bench.presentation.report import write_bench_artifacts
from agent_forge.evaluation.api import (
    EvaluationComparison,
    load_json_if_exists,
    write_evaluation_artifacts,
)


class FileBenchArtifacts:

    def __init__(self) -> None:
        self._diagnose = DiagnoseBenchCase(JsonCaseEvidenceReader())

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
        return {
            "instance_id": result.instance_id,
            "model_name_or_path": f"agent-forge-{provider}-{model or 'default'}",
            "model_patch": (
                result.patch_path.read_text(encoding="utf-8")
                if result.patch_path.exists()
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
    def copy_patch(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            source.read_text(encoding="utf-8") if source.exists() else "",
            encoding="utf-8",
        )

    def finalize_case(self, result: BenchCaseResult) -> None:
        self._diagnose.attach(result)
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
    text = "".join(
        json.dumps(row, ensure_ascii=False) + "\n"
        for row in rows
    )
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
