"""Compatibility facade for the layered SWE-bench capability.

New code should enter through :mod:`agent_forge.bench.api`. Private helpers
remain here only for older tests and integrations; they are not the main flow.
"""

from __future__ import annotations

from pathlib import Path

from agent_forge.bench.adapters.case_runtime import (
    DirectModelBaseline,
    LocalCaseExecutor,
    extract_diff as _extract_diff,
    looks_like_diff as _looks_like_diff,
    render_case_task as _render_case_task,
)
from agent_forge.bench.adapters.dataset import load_cases
from agent_forge.bench.adapters.git_workspace import (
    SwebenchWorkspaceManager,
    collect_patch as _git_diff,
    ensure_clean_git as _ensure_clean_git,
    repo_url_and_cache_key as _repo_url_and_cache_key,
)
from agent_forge.bench.adapters.official_evaluator import (
    run_official_evaluation as _run_official_evaluation,
)
from agent_forge.bench.api import run_swebench
from agent_forge.bench.domain.catalog import (
    DEFAULT_DATASET,
    REGRESSION_SETS,
    SHOWCASE_INSTANCE_ID,
    SHOWCASE_INSTANCE_NOTE,
)
from agent_forge.bench.domain.config import SwebenchRunRequest, safe_id as _safe_id
from agent_forge.bench.domain.models import BenchCase, BenchCaseResult, BenchRunSummary
from agent_forge.bench.presentation.cli import build_swebench_parser, run_swebench_from_args
from agent_forge.evaluation.api import (
    compare_runs,
    extract_run_metrics,
    load_json_if_exists,
    write_evaluation_artifacts,
)


def _request(
    *,
    provider: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    max_steps: int,
    max_context_chars: int,
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
) -> SwebenchRunRequest:
    return SwebenchRunRequest(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        max_steps=max_steps,
        max_context_chars=max_context_chars,
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


def _run_case(
    case: BenchCase,
    manager: SwebenchWorkspaceManager,
    output_dir: Path,
    provider: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    max_steps: int,
    max_context_chars: int,
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
) -> BenchCaseResult:
    """Legacy helper delegating to the typed case executor."""

    request = _request(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        max_steps=max_steps,
        max_context_chars=max_context_chars,
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
    return LocalCaseExecutor(manager).run(
        case,
        case_dir=output_dir / "cases" / _safe_id(case.instance_id),
        agent_mode=agent_mode,
        request=request,
    )


def _direct_baseline_prediction(
    case: BenchCase,
    provider: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
) -> dict:
    """Legacy helper delegating to the no-tools baseline adapter."""

    request = _request(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        max_steps=1,
        max_context_chars=1,
    )
    return DirectModelBaseline().predict(case, request)


def _run_compare_case(
    case: BenchCase,
    manager: SwebenchWorkspaceManager,
    output_dir: Path,
    provider: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    max_steps: int,
    max_context_chars: int,
    profile: str,
    max_revision_rounds: int,
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
) -> BenchCaseResult:
    """Legacy compare helper retained while callers migrate to ``RunSwebench``."""

    case_root = output_dir / "cases" / _safe_id(case.instance_id)
    single = _run_case(
        case=case,
        manager=manager,
        output_dir=case_root / "single",
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        max_steps=max_steps,
        max_context_chars=max_context_chars,
        agent_mode="single",
        profile=profile,
        max_revision_rounds=0,
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
    multi = _run_case(
        case=case,
        manager=manager,
        output_dir=case_root / "multi",
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        max_steps=max_steps,
        max_context_chars=max_context_chars,
        agent_mode="multi",
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
    comparison = compare_runs(
        case.instance_id,
        extract_run_metrics(
            single.to_dict(),
            load_json_if_exists(single.trace_path.parent / "usage.json"),
        ),
        extract_run_metrics(
            multi.to_dict(),
            load_json_if_exists(multi.trace_path.parent / "usage.json"),
            load_json_if_exists(
                multi.trace_path.parent / "multi_agent" / "multi_agent_summary.json"
            ),
        ),
    )
    write_evaluation_artifacts(comparison, case_root)
    combined_patch = case_root / "patch.diff"
    combined_patch.parent.mkdir(parents=True, exist_ok=True)
    combined_patch.write_text(
        multi.patch_path.read_text(encoding="utf-8")
        if multi.patch_path.exists()
        else "",
        encoding="utf-8",
    )
    return BenchCaseResult(
        instance_id=case.instance_id,
        repo=case.repo,
        workspace=multi.workspace,
        trace_path=multi.trace_path,
        usage_report_path=multi.usage_report_path,
        patch_path=combined_patch,
        status=multi.status,
        final_answer=multi.final_answer,
        patch_chars=multi.patch_chars,
        error=multi.error,
        evaluation_status=multi.evaluation_status,
        local_validation_status=multi.local_validation_status,
        local_validation_evidence=multi.local_validation_evidence,
        official_evaluation_status=multi.official_evaluation_status,
        official_evaluation_report_path=multi.official_evaluation_report_path,
        official_evaluation_detail=multi.official_evaluation_detail,
        failure_class=multi.failure_class or single.failure_class,
        diagnosis=multi.diagnosis or single.diagnosis,
        diagnosis_evidence=[
            *single.diagnosis_evidence[:2],
            *multi.diagnosis_evidence[:2],
        ],
        next_actions=multi.next_actions or single.next_actions,
    )


def _agent_variant_name(agent_mode: str) -> str:
    return "multi_agent" if agent_mode in {"multi", "compare"} else "agent_runtime"


def _write_latest_pointer(output_dir: Path) -> None:
    latest = Path(".agent_forge/latest")
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "bench.txt").write_text(str(output_dir), encoding="utf-8")


__all__ = [
    "DEFAULT_DATASET",
    "REGRESSION_SETS",
    "SHOWCASE_INSTANCE_ID",
    "SHOWCASE_INSTANCE_NOTE",
    "SwebenchWorkspaceManager",
    "build_swebench_parser",
    "load_cases",
    "run_swebench",
    "run_swebench_from_args",
]
