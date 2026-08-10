from __future__ import annotations

from dataclasses import dataclass

from agent_forge.bench.ports.benchmark import (
    BenchArtifactPort,
    CaseExecutorPort,
    CaseSourcePort,
    OfficialEvaluatorPort,
)


@dataclass(frozen=True)
class BenchDependencies:
    """RunSwebench 依赖的 Port 集合；具体 Adapter 统一由 wiring 注入。"""

    cases: CaseSourcePort
    executor: CaseExecutorPort
    official_evaluator: OfficialEvaluatorPort
    artifacts: BenchArtifactPort
