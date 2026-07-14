from __future__ import annotations

from pathlib import Path

from agent_forge.bench.adapters.artifact_files import FileBenchArtifacts
from agent_forge.bench.adapters.case_runtime import DirectModelBaseline, LocalCaseExecutor
from agent_forge.bench.adapters.dataset import SwebenchCaseSource
from agent_forge.bench.adapters.git_workspace import SwebenchWorkspaceManager
from agent_forge.bench.adapters.official_evaluator import SwebenchOfficialEvaluator
from agent_forge.bench.application.dependencies import BenchDependencies
from agent_forge.bench.application.swebench import RunSwebench
from agent_forge.bench.domain.config import BenchRunLayout, SwebenchRunRequest


def build_swebench_runner(
    request: SwebenchRunRequest,
    layout: BenchRunLayout,
    *,
    artifacts: FileBenchArtifacts | None = None,
) -> RunSwebench:
    """Compose the benchmark use case with local filesystem/runtime adapters."""

    artifact_adapter = artifacts or FileBenchArtifacts()
    workspace_manager = SwebenchWorkspaceManager(
        repo_cache=Path(request.repo_cache),
        output_dir=layout.output_dir,
    )
    return RunSwebench(
        BenchDependencies(
            cases=SwebenchCaseSource(),
            executor=LocalCaseExecutor(workspace_manager),
            baseline=DirectModelBaseline(),
            official_evaluator=SwebenchOfficialEvaluator(),
            artifacts=artifact_adapter,
        )
    )
