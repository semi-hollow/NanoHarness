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


class OfficialEvaluatorPort(Protocol):
    def evaluate(self, summary: BenchRunSummary, request: SwebenchRunRequest) -> None: ...


class CaseEvidenceReader(Protocol):
    def load_usage(self, result: BenchCaseResult) -> dict[str, Any]: ...

    def load_trace(self, result: BenchCaseResult) -> dict[str, Any]: ...


class BenchArtifactPort(Protocol):
    """Benchmark 产物边界；调用方只依赖该契约。

    Python 支持按方法形状隐式满足 Protocol；本项目的正式内置 Adapter 仍显式继承
    对应 Port，方便 IDE 查看层级并让类型检查尽早发现漏实现。测试替身可保持结构化实现。
    """

    def create_layout(
        self,
        output_root: str,
        run_id: str,
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

    def copy_candidate_diff(self, source: Path, destination: Path) -> None: ...

    def finalize_case(self, result: BenchCaseResult) -> None: ...

    def write_predictions(
        self,
        summary: BenchRunSummary,
        predictions: list[dict[str, Any]],
    ) -> None: ...

    def publish_run(
        self,
        summary: BenchRunSummary,
        predictions: list[dict[str, Any]],
    ) -> None: ...
