from __future__ import annotations

from dataclasses import dataclass

from agent_forge.bench.ports.benchmark import (
    BenchArtifactPort,
    CaseExecutorPort,
    CaseSourcePort,
    DirectBaselinePort,
    OfficialEvaluatorPort,
)


@dataclass(frozen=True)
class BenchDependencies:

    cases: CaseSourcePort
    executor: CaseExecutorPort
    baseline: DirectBaselinePort
    official_evaluator: OfficialEvaluatorPort
    artifacts: BenchArtifactPort
