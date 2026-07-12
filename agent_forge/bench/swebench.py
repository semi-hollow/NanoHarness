from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Iterable

from agent_forge.evaluation import compare_runs, compare_variants, extract_run_metrics, load_json_if_exists, write_evaluation_artifacts
from agent_forge.multi_agent import MultiAgentCoordinator, get_profile
from agent_forge.models.gateway import ModelGateway
from agent_forge.observability.trace import TraceRecorder
from agent_forge.observability.usage_report import write_usage_artifacts
from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.execution_environment import ExecutionEnvironment, ExecutionEnvironmentConfig
from agent_forge.runtime.git_workspace import collect_workspace_diff
from agent_forge.runtime.llm_config import resolve_llm_config
from agent_forge.runtime.message import Message
from agent_forge.runtime.wiring import build_llm, build_registry

from .case_study import write_case_study
from .diagnostics import attach_failure_diagnosis
from .evidence import read_local_validation
from .official_results import apply_official_results, parse_official_results
from .report import write_bench_artifacts
from .types import BenchCase, BenchCaseResult, BenchRunSummary


DEFAULT_DATASET = "princeton-nlp/SWE-bench_Lite"
SHOWCASE_INSTANCE_ID = "astropy__astropy-12907"
SHOWCASE_INSTANCE_NOTE = (
    "Astropy nested CompoundModel separability bug. This case is small enough "
    "for local runs but forces real repository checkout, context retrieval, "
    "tool use, patch generation, and trace/usage inspection."
)
REGRESSION_SETS = {
    "core": [
        SHOWCASE_INSTANCE_ID,
        "django__django-11133",
        "matplotlib__matplotlib-18869",
        "pytest-dev__pytest-5103",
        "sympy__sympy-20590",
    ]
}


def load_cases(
    dataset_name: str,
    split: str,
    limit: int,
    instance_ids: list[str] | None = None,
    cases_file: str | None = None,
) -> list[BenchCase]:
    """Load SWE-bench rows from a local JSONL file or HuggingFace datasets.

    Local JSONL is useful for dry runs and CI. HuggingFace loading is the normal
    path for real SWE-bench Lite/Verified runs, but it is optional so the core
    package stays lightweight.
    """

    wanted = set(instance_ids or [])
    raw_cases = _load_cases_file(cases_file) if cases_file else _load_huggingface_cases(dataset_name, split)
    cases = []
    for raw in raw_cases:
        case = BenchCase.from_mapping(raw)
        if wanted and case.instance_id not in wanted:
            continue
        cases.append(case)
        if limit and len(cases) >= limit:
            break
    if not cases:
        raise RuntimeError("No SWE-bench cases matched the requested filters.")
    return cases


def _load_cases_file(cases_file: str | None) -> list[dict]:
    """Read JSONL or JSON list cases from disk."""

    path = Path(cases_file or "")
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("JSON cases file must contain a list of objects.")
        return [dict(item) for item in data]
    rows = []
    for line in text.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _load_huggingface_cases(dataset_name: str, split: str) -> list[dict]:
    """Load public SWE-bench data when the optional datasets package exists."""

    if importlib.util.find_spec("datasets") is None:
        raise RuntimeError(
            "Install benchmark extras first: python -m pip install -e '.[bench]'. "
            "Alternatively pass --cases-file with SWE-bench-shaped JSONL rows."
        )
    from datasets import load_dataset

    dataset = load_dataset(dataset_name, split=split)
    return [dict(row) for row in dataset]


class SwebenchWorkspaceManager:
    """Clone repos once and create per-case worktrees at base commits.

    Why it exists:
        SWE-bench is meaningless without exact base-commit reproduction. This
        manager keeps cloned repositories in a cache and creates a clean
        detached worktree for every case, so each run starts from the benchmark
        state instead of whatever files happened to be left locally.
    """

    def __init__(self, repo_cache: Path, output_dir: Path) -> None:
        self.repo_cache = repo_cache.resolve()
        self.output_dir = output_dir.resolve()

    def prepare(self, case: BenchCase, variant: str = "") -> Path:
        """Return a clean workspace checked out at the case base commit."""

        source = self._ensure_repo(case.repo)
        suffix = f"__{_safe_id(variant)}" if variant else ""
        workspace = self.output_dir / "workspaces" / f"{_safe_id(case.instance_id)}{suffix}"
        workspace.parent.mkdir(parents=True, exist_ok=True)
        self._run(["git", "-C", str(source), "worktree", "prune"], check=False)
        result = self._run(
            ["git", "-C", str(source), "worktree", "add", "--detach", str(workspace), case.base_commit],
            check=False,
        )
        if result.returncode != 0:
            self._run(["git", "-C", str(source), "fetch", "origin", case.base_commit], check=False)
            self._run(
                ["git", "-C", str(source), "worktree", "add", "--detach", str(workspace), case.base_commit],
                check=True,
            )
        return workspace

    def _ensure_repo(self, repo: str) -> Path:
        """Clone the GitHub repo into the cache if it is not present."""

        self.repo_cache.mkdir(parents=True, exist_ok=True)
        url, cache_key = _repo_url_and_cache_key(repo)
        target = self.repo_cache / cache_key
        if (target / ".git").exists():
            self._run(["git", "-C", str(target), "fetch", "--all", "--tags", "--prune"], check=False)
            return target
        self._run(["git", "clone", url, str(target)], check=True)
        return target

    def _run(self, command: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        """Run git commands with captured output for readable errors."""

        result = subprocess.run(command, text=True, capture_output=True)
        if check and result.returncode != 0:
            raise RuntimeError(
                f"command failed: {' '.join(command)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result


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
    """Generate SWE-bench patch predictions with Agent Forge.

    This function is the project effect loop: external benchmark case -> clean
    repo checkout -> AgentLoop -> git diff -> predictions.jsonl -> optional
    official SWE-bench Docker evaluation.
    """

    run_id = f"swebench-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:7]}"
    output_dir = (Path(output_root) / run_id).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = load_cases(dataset_name, split, limit, instance_ids, cases_file)
    predictions_path = output_dir / "predictions.jsonl"
    baseline_predictions_path = output_dir / "direct_baseline_predictions.jsonl" if direct_baseline else None
    manager = SwebenchWorkspaceManager(Path(repo_cache), output_dir)

    summary = BenchRunSummary(
        run_id=run_id,
        dataset_name=dataset_name,
        split=split,
        provider=provider,
        model=model or "",
        output_dir=output_dir,
        predictions_path=predictions_path,
        agent_mode=agent_mode,
        profile=profile if agent_mode in {"multi", "compare"} else "",
        max_revision_rounds=max_revision_rounds if agent_mode in {"multi", "compare"} else 0,
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
        max_steps=max_steps,
        max_context_chars=max_context_chars,
        baseline_predictions_path=baseline_predictions_path,
        notes=[
            "Generated patches are not resolved-rate claims until the official SWE-bench harness evaluates them.",
            "Repo workspaces are under .agent_forge/runs so the main checkout stays clean.",
        ],
    )
    baseline_predictions: dict[str, dict] = {}

    with predictions_path.open("w", encoding="utf-8") as prediction_file:
        baseline_file = baseline_predictions_path.open("w", encoding="utf-8") if baseline_predictions_path else None
        try:
            for case in cases:
                if agent_mode == "compare":
                    result = _run_compare_case(
                        case=case,
                        manager=manager,
                        output_dir=output_dir,
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
                else:
                    result = _run_case(
                        case=case,
                        manager=manager,
                        output_dir=output_dir,
                        provider=provider,
                        model=model,
                        base_url=base_url,
                        api_key=api_key,
                        max_steps=max_steps,
                        max_context_chars=max_context_chars,
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
                summary.case_results.append(result)
                prediction_file.write(
                    json.dumps(
                        {
                            "instance_id": case.instance_id,
                            "model_name_or_path": f"agent-forge-{provider}-{model or 'default'}",
                            "model_patch": result.patch_path.read_text(encoding="utf-8")
                            if result.patch_path.exists()
                            else "",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                prediction_file.flush()
                if baseline_file:
                    baseline_prediction = _direct_baseline_prediction(case, provider, model, base_url, api_key)
                    baseline_file.write(json.dumps(baseline_prediction, ensure_ascii=False) + "\n")
                    baseline_file.flush()
                    baseline_predictions[case.instance_id] = baseline_prediction
        finally:
            if baseline_file:
                baseline_file.close()

    if evaluate:
        _run_official_evaluation(summary, max_workers=max_workers, namespace_empty=namespace_empty)

    for result in summary.case_results:
        attach_failure_diagnosis(result)
        write_case_study(result)
        stored_baseline_prediction = baseline_predictions.get(result.instance_id)
        if stored_baseline_prediction:
            agent_metrics = extract_run_metrics(
                result.to_dict(),
                load_json_if_exists(result.trace_path.parent / "usage.json"),
            )
            summary.variant_comparisons[result.instance_id] = compare_variants(
                result.instance_id,
                {
                    "direct_baseline": stored_baseline_prediction,
                    _agent_variant_name(summary.agent_mode): agent_metrics,
                },
            )

    write_bench_artifacts(summary)
    _write_latest_pointer(output_dir)
    return summary


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
    """Run AgentLoop on one clean SWE-bench workspace and capture the diff."""

    workspace = manager.prepare(case, agent_mode if agent_mode in {"single", "multi"} else "")
    active_workspace = workspace
    case_dir = output_dir / "cases" / _safe_id(case.instance_id)
    case_dir.mkdir(parents=True, exist_ok=True)
    trace_path = case_dir / "trace.json"
    patch_path = case_dir / "patch.diff"
    final_answer = ""
    usage_report_path = None
    status = "blocked"
    error = ""
    environment: ExecutionEnvironment | None = None

    try:
        _ensure_clean_git(workspace)
        task = _render_case_task(case)
        trace = TraceRecorder(str(trace_path))
        environment = ExecutionEnvironment(
            ExecutionEnvironmentConfig(
                mode=execution_mode,
                workspace=str(workspace),
                run_id=f"{_safe_id(case.instance_id)}-{agent_mode}-{uuid.uuid4().hex[:7]}",
                network_policy=network_policy,
                keep_worktree=keep_worktree,
                container_runtime=container_runtime,
                container_image=container_image,
                container_cpus=container_cpus,
                container_memory=container_memory,
                container_pids_limit=container_pids_limit,
                container_read_only=container_read_only,
            )
        )
        environment.prepare()
        active_workspace = environment.active_workspace
        registry = build_registry(str(active_workspace), auto=True, execution_environment=environment)
        llm_config = resolve_llm_config(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=60,
        )
        if not llm_config.is_configured():
            raise RuntimeError(f"{provider} model config is incomplete; set API key/base URL/model.")
        llm = build_llm(llm_config)
        runtime_config = RuntimeConfig(
            workspace=str(active_workspace),
            max_steps=max_steps,
            trace_file=str(trace_path),
            max_context_chars=max_context_chars,
            timeout_seconds=900,
            task_state_root=str(case_dir / "task_state"),
            tool_routing_mode=tool_routing_mode,
            execution_environment=environment,
        )
        if agent_mode == "multi":
            multi_profile = get_profile(profile)
            final_answer = MultiAgentCoordinator(
                task,
                multi_profile,
                runtime_config,
                trace,
                registry,
                llm,
                run_dir=case_dir,
                max_revision_rounds=max_revision_rounds,
            ).run().final_answer
        else:
            final_answer = AgentLoop(runtime_config, trace, registry, llm).run(task)
        trace.write()
        _, usage_report_path = write_usage_artifacts(trace_path)
        patch = _git_diff(active_workspace)
        patch_path.write_text(patch, encoding="utf-8")
        if patch.strip():
            status = "patch_generated"
        elif final_answer.startswith("blocked:"):
            status = "blocked"
        else:
            status = "no_patch"
    except Exception as exc:
        error = str(exc)
        patch_path.write_text("", encoding="utf-8")
        if not trace_path.exists():
            trace_path.write_text(json.dumps({"error": error}, indent=2), encoding="utf-8")
    finally:
        if environment is not None:
            try:
                environment.write_manifest(case_dir)
            except Exception as exc:
                detail = f"execution manifest failed: {exc}"
                error = f"{error}; {detail}" if error else detail
            try:
                environment.cleanup()
            except Exception as exc:
                detail = f"execution cleanup failed: {exc}"
                error = f"{error}; {detail}" if error else detail

    local_validation = read_local_validation(trace_path)
    return BenchCaseResult(
        instance_id=case.instance_id,
        repo=case.repo,
        workspace=active_workspace,
        trace_path=trace_path,
        usage_report_path=usage_report_path,
        patch_path=patch_path,
        status=status,
        final_answer=final_answer,
        patch_chars=len(patch_path.read_text(encoding="utf-8")) if patch_path.exists() else 0,
        error=error,
        evaluation_status="local_verified" if local_validation.status == "passed" else "not_evaluated",
        local_validation_status=local_validation.status,
        local_validation_evidence=local_validation.evidence,
    )


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
    """Run one case in isolated single and multi variants and write comparison artifacts."""

    case_root = output_dir / "cases" / _safe_id(case.instance_id)
    single_result = _run_case(
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
    multi_result = _run_case(
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
            single_result.to_dict(),
            load_json_if_exists(single_result.trace_path.parent / "usage.json"),
        ),
        extract_run_metrics(
            multi_result.to_dict(),
            load_json_if_exists(multi_result.trace_path.parent / "usage.json"),
            load_json_if_exists(multi_result.trace_path.parent / "multi_agent" / "multi_agent_summary.json"),
        ),
    )
    write_evaluation_artifacts(comparison, case_root)
    combined_patch = case_root / "patch.diff"
    combined_patch.write_text(multi_result.patch_path.read_text(encoding="utf-8"), encoding="utf-8")
    return BenchCaseResult(
        instance_id=case.instance_id,
        repo=case.repo,
        workspace=multi_result.workspace,
        trace_path=multi_result.trace_path,
        usage_report_path=multi_result.usage_report_path,
        patch_path=combined_patch,
        status=multi_result.status,
        final_answer=multi_result.final_answer,
        patch_chars=multi_result.patch_chars,
        error=multi_result.error,
        evaluation_status=multi_result.evaluation_status,
        local_validation_status=multi_result.local_validation_status,
        local_validation_evidence=multi_result.local_validation_evidence,
        official_evaluation_status=multi_result.official_evaluation_status,
        official_evaluation_report_path=multi_result.official_evaluation_report_path,
        official_evaluation_detail=multi_result.official_evaluation_detail,
        failure_class=multi_result.failure_class or single_result.failure_class,
        diagnosis=multi_result.diagnosis or single_result.diagnosis,
        diagnosis_evidence=[*single_result.diagnosis_evidence[:2], *multi_result.diagnosis_evidence[:2]],
        next_actions=multi_result.next_actions or single_result.next_actions,
    )


def _render_case_task(case: BenchCase) -> str:
    """Create the agent-facing SWE-bench task prompt."""

    return (
        "Resolve this SWE-bench coding issue.\n\n"
        f"Instance: {case.instance_id}\n"
        f"Repository: {case.repo}\n"
        f"Base commit: {case.base_commit}\n\n"
        "Issue:\n"
        f"{case.problem_statement}\n\n"
        "Operating rules:\n"
        "- Inspect the repository before editing.\n"
        "- Make the smallest source-code patch that addresses the issue.\n"
        "- Do not edit tests unless the issue explicitly requires test infrastructure changes.\n"
        "- Use read_file/grep_search for source inspection; do not use run_command for reading files.\n"
        "- Prefer apply_patch once the likely target function is identified; do not keep gathering broad evidence.\n"
        "- Prefer diagnostics for focused validation. Do not use python -c, shell pipes, redirection, or /tmp files.\n"
        "- If validation is blocked, keep the patch and clearly explain the unverified point instead of spending more steps.\n"
        "- Finish with a concise summary grounded in files changed and commands run.\n"
    )


def _direct_baseline_prediction(
    case: BenchCase,
    provider: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
) -> dict:
    """Generate a no-tools baseline patch from the issue text only.

    This is intentionally weaker than AgentLoop. It answers the architecture
    question "why not just prompt the model once?" with a measured baseline.
    """

    llm_config = resolve_llm_config(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=60,
    )
    if not llm_config.is_configured():
        return {
            "instance_id": case.instance_id,
            "model_name_or_path": f"direct-{provider}-{model or 'default'}",
            "model_patch": "",
            "error": f"{provider} model config is incomplete",
        }
    llm = build_llm(llm_config)
    response = llm.chat(
        [
            Message(
                "system",
                "You are a coding model baseline. Return only a unified diff patch. Do not explain.",
            ),
            Message(
                "user",
                f"Repository: {case.repo}\nBase commit: {case.base_commit}\nIssue:\n{case.problem_statement}",
            ),
        ],
        [],
    )
    usage = {}
    if getattr(llm, "last_usage", None) is not None:
        usage = llm.last_usage.to_dict()
    model_patch = _extract_diff(response.content or "")
    return {
        "instance_id": case.instance_id,
        "model_name_or_path": f"direct-{provider}-{model or 'default'}",
        "model_patch": model_patch,
        "error": response.error or "",
        "failure_class": "baseline_provider_error" if response.error else "" if model_patch else "no_patch_generated",
        "estimated_cost_usd": float(usage.get("estimated_cost_usd") or 0.0),
        "llm_calls": 1,
        "total_tokens": int(usage.get("total_tokens") or 0),
        "llm_latency_ms": int(usage.get("latency_ms") or 0),
        "tool_calls": 0,
        "failed_tool_calls": 0,
    }


def _run_official_evaluation(summary: BenchRunSummary, max_workers: int, namespace_empty: bool) -> None:
    """Call the official SWE-bench harness when installed."""

    if importlib.util.find_spec("swebench") is None:
        summary.official_eval_exit_code = 127
        summary.official_eval_output = "swebench package is not installed. Install SWE-bench and rerun with --evaluate."
        for result in summary.case_results:
            result.official_evaluation_status = "official_eval_unavailable"
            result.official_evaluation_detail = summary.official_eval_output
            result.evaluation_status = "official_eval_unavailable"
        return
    cmd = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        summary.dataset_name,
        "--split",
        summary.split,
        "--predictions_path",
        str(summary.predictions_path),
        "--max_workers",
        str(max_workers),
        "--run_id",
        summary.run_id,
    ]
    instance_ids = [result.instance_id for result in summary.case_results]
    if instance_ids:
        cmd.extend(["--instance_ids", *instance_ids])
    if namespace_empty or (platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}):
        cmd.extend(["--namespace", ""])
    summary.official_eval_command = cmd
    evaluation_process = subprocess.run(cmd, text=True, capture_output=True, cwd=str(summary.output_dir))
    summary.official_eval_exit_code = evaluation_process.returncode
    output = f"STDOUT:\n{evaluation_process.stdout}\nSTDERR:\n{evaluation_process.stderr}"
    summary.official_eval_output = output[-20000:]
    parsed = parse_official_results(summary.output_dir, summary.run_id, instance_ids)
    summary.official_eval_report_path = str(parsed.report_path or "")
    summary.official_eval_warnings = parsed.warnings
    apply_official_results(
        summary.case_results,
        parsed,
        process_exit_code=evaluation_process.returncode,
    )


def _ensure_clean_git(workspace: Path) -> None:
    """Make sure a generated case workspace starts without local changes."""

    subprocess.run(["git", "-C", str(workspace), "reset", "--hard"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(workspace), "clean", "-fdx"], check=True, capture_output=True, text=True)


def _git_diff(workspace: Path) -> str:
    """Return the candidate SWE-bench patch."""

    return collect_workspace_diff(workspace)


def _extract_diff(text: str) -> str:
    """Strip Markdown fences from direct baseline output when present."""

    stripped = text.strip()
    if "```" not in stripped:
        return stripped if _looks_like_diff(stripped) else ""
    chunks = stripped.split("```")
    for chunk in chunks:
        candidate = chunk.strip()
        if candidate.startswith("diff"):
            candidate = candidate[4:].strip()
        if _looks_like_diff(candidate):
            return candidate
    return ""


def _looks_like_diff(text: str) -> bool:
    """Return true only for unified/git-diff-shaped model output."""

    stripped = text.strip()
    return stripped.startswith("diff --git ") or (
        stripped.startswith("--- ") and "\n+++ " in stripped
    )


def _agent_variant_name(agent_mode: str) -> str:
    """Name the actual agent run without pretending an unrun variant exists."""

    if agent_mode == "multi" or agent_mode == "compare":
        return "multi_agent"
    return "agent_runtime"


def _write_latest_pointer(output_dir: Path) -> None:
    """Update a stable pointer for ``forge report latest``."""

    latest = Path(".agent_forge/latest")
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "bench.txt").write_text(str(output_dir), encoding="utf-8")


def _repo_url_and_cache_key(repo: str) -> tuple[str, str]:
    """Return clone URL plus cache key for GitHub ids or local smoke repos."""

    if repo.startswith("file://"):
        local_path_text = repo.removeprefix("file://")
        return repo, f"local__{_safe_id(local_path_text)}"
    local_path = Path(repo)
    if local_path.exists():
        return str(local_path.resolve()), f"local__{_safe_id(str(local_path.resolve()))}"
    return f"https://github.com/{repo}.git", repo.replace("/", "__")


def _safe_id(value: str) -> str:
    """Convert benchmark ids into filesystem-safe path fragments."""

    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)


def build_swebench_parser(parser: argparse.ArgumentParser) -> None:
    """Attach SWE-bench options to a subparser."""

    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--split", default="test")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument(
        "--showcase",
        action="store_true",
        help=f"Run the fixed reference case {SHOWCASE_INSTANCE_ID} for repeatable before/after comparisons. {SHOWCASE_INSTANCE_NOTE}",
    )
    parser.add_argument(
        "--regression-set",
        choices=sorted(REGRESSION_SETS),
        help="Run a named fixed SWE-bench case set for before/after harness regression checks.",
    )
    parser.add_argument("--cases-file")
    parser.add_argument("--provider", default=os.getenv("AGENT_FORGE_DEFAULT_LLM", "deepseek"))
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--max-steps", type=int, default=16)
    parser.add_argument("--max-context-chars", type=int, default=12000)
    parser.add_argument("--repo-cache", default=".agent_forge/bench/repos")
    parser.add_argument("--output-root", default=".agent_forge/runs")
    parser.add_argument("--direct-baseline", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--namespace-empty", action="store_true")
    parser.add_argument("--agent-mode", default="single", choices=["single", "multi", "compare"])
    parser.add_argument("--profile", default="coding_fix", choices=["coding_fix"])
    parser.add_argument("--max-revision-rounds", type=int, default=2)
    parser.add_argument(
        "--tool-routing",
        choices=["task-aware", "all"],
        default="task-aware",
        help="Select task-aware tool visibility or expose all tools for a controlled ablation; runtime safety policy remains enabled.",
    )
    parser.add_argument(
        "--execution-mode",
        choices=["local", "worktree", "container"],
        default="local",
        help="Run each case locally, in an extra detached worktree, or in a constrained OCI container.",
    )
    parser.add_argument("--network-policy", choices=["deny", "allow"], default="deny")
    parser.add_argument(
        "--keep-worktree",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Retain the extra per-case execution snapshot; benchmark base workspaces remain in the run directory.",
    )
    parser.add_argument("--container-runtime", default="docker")
    parser.add_argument("--container-image", default="python:3.11-slim")
    parser.add_argument("--container-cpus", type=float, default=1.0)
    parser.add_argument("--container-memory", default="1g")
    parser.add_argument("--container-pids-limit", type=int, default=256)
    parser.add_argument(
        "--container-read-only",
        action=argparse.BooleanOptionalAction,
        default=True,
    )


def run_swebench_from_args(args: argparse.Namespace) -> BenchRunSummary:
    """CLI adapter for ``forge bench swebench``."""

    instance_ids = args.instance_id
    limit = args.limit
    if args.regression_set and not instance_ids:
        instance_ids = REGRESSION_SETS[args.regression_set]
        limit = len(instance_ids)
    elif args.showcase and not instance_ids:
        instance_ids = [SHOWCASE_INSTANCE_ID]
        limit = 1

    return run_swebench(
        dataset_name=args.dataset,
        split=args.split,
        limit=limit,
        instance_ids=instance_ids,
        cases_file=args.cases_file,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        max_steps=args.max_steps,
        max_context_chars=args.max_context_chars,
        repo_cache=args.repo_cache,
        output_root=args.output_root,
        direct_baseline=args.direct_baseline,
        evaluate=args.evaluate,
        max_workers=args.max_workers,
        namespace_empty=args.namespace_empty,
        agent_mode=args.agent_mode,
        profile=args.profile,
        max_revision_rounds=args.max_revision_rounds,
        tool_routing_mode=args.tool_routing,
        execution_mode=args.execution_mode,
        network_policy=args.network_policy,
        keep_worktree=args.keep_worktree,
        container_runtime=args.container_runtime,
        container_image=args.container_image,
        container_cpus=args.container_cpus,
        container_memory=args.container_memory,
        container_pids_limit=args.container_pids_limit,
        container_read_only=args.container_read_only,
    )
