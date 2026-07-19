from __future__ import annotations

from pathlib import Path
from agent_forge.bench.adapters.artifact_files import FileBenchArtifacts
from agent_forge.bench.adapters.campaign_files import (
    FileCampaignArtifacts,
    GitSourceIdentity,
)
from agent_forge.bench.adapters.case_runtime import DirectModelBaseline, LocalCaseExecutor
from agent_forge.bench.adapters.dataset import SwebenchCaseSource
from agent_forge.bench.adapters.git_workspace import SwebenchWorkspaceManager
from agent_forge.bench.adapters.official_evaluator import SwebenchOfficialEvaluator
from agent_forge.bench.application.dependencies import BenchDependencies
from agent_forge.bench.application.campaign import RunBenchmarkCampaign
from agent_forge.bench.application.swebench import RunSwebench
from agent_forge.bench.domain.config import BenchRunLayout, SwebenchRunRequest
from agent_forge.bench.domain.models import BenchRunSummary
from agent_forge.bench.ports import BenchmarkRunnerPort


def build_swebench_runner(
    request: SwebenchRunRequest,
    layout: BenchRunLayout,
    *,
    artifacts: FileBenchArtifacts | None = None,
) -> RunSwebench:

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


def build_benchmark_campaign_runner(
    project_dir: Path,
    runner: BenchmarkRunnerPort,
) -> RunBenchmarkCampaign:
    """组合 campaign 应用层与 Git/File adapters；不改变单次 benchmark runner。"""

    return RunBenchmarkCampaign(
        runner,
        FileCampaignArtifacts(project_dir),
        GitSourceIdentity(project_dir),
    )
