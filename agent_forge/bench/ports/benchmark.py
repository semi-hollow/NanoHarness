from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from agent_forge.bench.domain.config import BenchRunLayout, SwebenchRunRequest
from agent_forge.bench.domain.models import BenchCase, BenchCaseResult, BenchRunSummary
from agent_forge.evaluation.api import EvaluationComparison


class CaseSourcePort(Protocol):
    def load(self, request: SwebenchRunRequest) -> list[BenchCase]: ...


class CaseExecutorPort(Protocol):
    def run(
        self,
        case: BenchCase,
        *,
        case_dir: Path,
        agent_mode: str,
        request: SwebenchRunRequest,
    ) -> BenchCaseResult: ...


class DirectBaselinePort(Protocol):
    def predict(
        self,
        case: BenchCase,
        request: SwebenchRunRequest,
    ) -> dict[str, Any]: ...


class OfficialEvaluatorPort(Protocol):
    def evaluate(self, summary: BenchRunSummary, request: SwebenchRunRequest) -> None: ...


class CaseEvidenceReader(Protocol):
    def load_usage(self, result: BenchCaseResult) -> dict[str, Any]: ...

    def load_trace(self, result: BenchCaseResult) -> dict[str, Any]: ...


class BenchArtifactPort(Protocol):
    def create_layout(
        self,
        output_root: str,
        run_id: str,
        *,
        include_baseline: bool,
    ) -> BenchRunLayout: ...

    def read_json(self, path: Path) -> dict[str, Any]: ...

    def prediction_for(
        self,
        result: BenchCaseResult,
        *,
        provider: str,
        model: str | None,
    ) -> dict[str, Any]: ...

    def write_comparison(
        self,
        comparison: EvaluationComparison,
        output_dir: Path,
    ) -> None: ...

    def copy_patch(self, source: Path, destination: Path) -> None: ...

    def finalize_case(self, result: BenchCaseResult) -> None: ...

    def write_predictions(
        self,
        summary: BenchRunSummary,
        predictions: list[dict[str, Any]],
        baseline_predictions: list[dict[str, Any]],
    ) -> None: ...

    def publish_run(
        self,
        summary: BenchRunSummary,
        predictions: list[dict[str, Any]],
        baseline_predictions: list[dict[str, Any]],
    ) -> None: ...
