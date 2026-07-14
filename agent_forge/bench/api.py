from __future__ import annotations

import time
import uuid

from agent_forge.bench.adapters.artifact_files import FileBenchArtifacts
from agent_forge.bench.domain.catalog import DEFAULT_DATASET, REGRESSION_SETS
from agent_forge.bench.domain.config import SwebenchRunRequest
from agent_forge.bench.domain.models import BenchRunSummary
from agent_forge.bench.wiring import build_swebench_runner


# PRIMARY ENTRYPOINT: build and execute one complete SWE-bench evidence run.
def run_swebench(
    dataset_name: str = DEFAULT_DATASET,
    split: str = "test",
    limit: int = 1,
    instance_ids: list[str] | None = None,
    cases_file: str | None = None,
    provider: str = "deepseek",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    max_steps: int = 16,
    max_context_chars: int = 12000,
    repo_cache: str = ".agent_forge/bench/repos",
    output_root: str = ".agent_forge/runs",
    direct_baseline: bool = False,
    evaluate: bool = False,
    max_workers: int = 1,
    namespace_empty: bool = False,
    agent_mode: str = "single",
    profile: str = "coding_fix",
    max_revision_rounds: int = 2,
    tool_routing_mode: str = "task-aware",
    execution_mode: str = "local",
    network_policy: str = "deny",
    keep_worktree: bool = False,
    container_runtime: str = "docker",
    container_image: str = "python:3.11-slim",
    container_cpus: float = 1.0,
    container_memory: str = "1g",
    container_pids_limit: int = 256,
    container_read_only: bool = True,
) -> BenchRunSummary:
    """Adapt the backwards-compatible function signature into a typed request."""

    request = SwebenchRunRequest(
        dataset_name=dataset_name,
        split=split,
        limit=limit,
        instance_ids=tuple(instance_ids or ()),
        cases_file=cases_file,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        max_steps=max_steps,
        max_context_chars=max_context_chars,
        repo_cache=repo_cache,
        output_root=output_root,
        direct_baseline=direct_baseline,
        evaluate=evaluate,
        max_workers=max_workers,
        namespace_empty=namespace_empty,
        agent_mode=agent_mode,
        profile=profile,
        max_revision_rounds=max_revision_rounds,
        tool_routing_mode=tool_routing_mode,
        execution_mode=execution_mode,
        network_policy=network_policy,
        keep_worktree=keep_worktree,
        container_runtime=container_runtime,
        container_image=container_image,
        container_cpus=container_cpus,
        container_memory=container_memory,
        container_pids_limit=container_pids_limit,
        container_read_only=container_read_only,
    )
    run_id = f"swebench-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:7]}"
    artifacts = FileBenchArtifacts()
    layout = artifacts.create_layout(
        request.output_root,
        run_id,
        include_baseline=request.direct_baseline,
    )
    return build_swebench_runner(
        request,
        layout,
        artifacts=artifacts,
    ).execute(request, run_id=run_id, layout=layout)


__all__ = ["DEFAULT_DATASET", "REGRESSION_SETS", "run_swebench"]
