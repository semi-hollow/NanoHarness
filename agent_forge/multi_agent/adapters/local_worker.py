"""基于本地 worktree 和真实 AgentLoop 的 fanout worker adapter。"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

from agent_forge.observability.adapters.json_trace import TraceRecorder
from agent_forge.observability.api import write_usage_artifacts
from agent_forge.runtime.api import build_agent_loop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.execution_environment import (
    ExecutionEnvironment,
    ExecutionEnvironmentConfig,
)
from agent_forge.runtime.git_workspace import collect_changed_files, collect_workspace_diff
from agent_forge.runtime.llm_client import LLMClient
from agent_forge.safety.guardrails import sanitize_quoted_evidence
from agent_forge.tools.registry import ToolRegistry

from ..domain.fanout import SubagentTask
from ..domain.live import (
    FanoutPlan,
    FinalizerResult,
    LiveSubagentResult,
)
from .git_workspace import apply_patch_to_workspace, commit_worker_baseline

READ_TOOLS = {
    "list_files",
    "read_file",
    "grep",
    "grep_search",
    "git_status",
    "git_diff",
    "diagnostics",
    "ask_human",
}
FINALIZER_READ_TOOLS = {"git_status", "git_diff", "diagnostics"}
WRITE_TOOLS = {*READ_TOOLS, "apply_patch", "write_file", "run_command"}

RegistryFactory = Callable[[Path, ExecutionEnvironment], ToolRegistry]
LLMFactory = Callable[[], LLMClient]


class LocalAgentWorkerAdapter:
    """执行隔离 worker、只读 finalizer 和恢复 patch 验证。"""

    def __init__(
        self,
        *,
        plan: FanoutPlan,
        base_config: RuntimeConfig,
        run_root: str | Path,
        run_id: str,
        base_head: str,
        llm_factory: LLMFactory,
        registry_factory: RegistryFactory,
    ) -> None:
        self.plan = plan
        self.base_config = base_config
        self.root = Path(run_root).resolve()
        self.run_id = run_id
        self.base_head = base_head
        self.llm_factory = llm_factory
        self.registry_factory = registry_factory
        self.workspace = Path(base_config.workspace).resolve()
        self._git_lock = threading.Lock()

    def run_worker(
        self,
        task: SubagentTask,
        batch_index: int,
        base_patch: str,
    ) -> LiveSubagentResult:
        """在临时 worktree 中运行一个受 scope 限制的 AgentLoop。"""

        started = time.monotonic()
        worker_dir = self.root / "workers" / task.id
        worker_dir.mkdir(parents=True, exist_ok=True)
        trace_path = worker_dir / "trace.json"
        patch_path = worker_dir / "patch.diff"
        artifact_path = worker_dir / f"{task.expected_artifact}.md"
        environment = ExecutionEnvironment(
            ExecutionEnvironmentConfig(
                mode="worktree",
                workspace=str(self.workspace),
                run_id=f"{self.run_id[:8]}-{task.id}",
                network_policy="deny",
                keep_worktree=False,
            )
        )
        active_workspace = self.workspace
        manifest_path = worker_dir / "execution_environment.json"
        final_answer = ""
        status = "failed"
        error = ""
        touched_files: list[str] = []
        usage_summary: dict[str, object] = {}
        patch_sha256 = ""
        try:
            with self._git_lock:
                environment.prepare()
            active_workspace = environment.active_workspace
            if base_patch.strip():
                ok, detail = apply_patch_to_workspace(
                    active_workspace,
                    base_patch,
                    check_only=False,
                )
                if not ok:
                    raise RuntimeError(
                        f"could not seed integrated patch into worker: {detail}"
                    )
                commit_worker_baseline(active_workspace)

            registry = _filtered_registry(
                self.registry_factory(active_workspace, environment),
                task,
            )
            worker_trace = TraceRecorder(str(trace_path))
            worker_config = replace(
                self.base_config,
                workspace=str(active_workspace),
                execution_environment=environment,
                max_steps=min(self.base_config.max_steps, task.max_steps),
                approval_mode=(
                    "dry-run" if not task.write_scope else self.base_config.approval_mode
                ),
                task_state_root=str(worker_dir / "task_state"),
                approval_root=str(worker_dir / "approvals"),
                human_thread_id=(
                    f"fanout:{self.plan.digest[:16]}:{self.base_head[:12]}:{task.id}"
                ),
                operation_ledger_root=str(worker_dir / "operation_ledger"),
            )
            final_answer = build_agent_loop(
                worker_config,
                worker_trace,
                registry,
                self.llm_factory(),
            ).run(
                worker_task_prompt(self.plan.goal, task),
                agent_name=f"Subagent:{task.id}",
            )
            worker_trace.write()
            usage_json, _ = write_usage_artifacts(trace_path)
            usage = json.loads(usage_json.read_text(encoding="utf-8"))
            usage_summary = dict(usage.get("summary") or {})
            patch = collect_workspace_diff(active_workspace)
            patch_path.write_text(patch, encoding="utf-8")
            patch_sha256 = hashlib.sha256(patch.encode("utf-8")).hexdigest()
            touched_files = collect_changed_files(active_workspace)
            status, error = _worker_status(task, final_answer, touched_files)
            artifact_path.write_text(
                _render_worker_artifact(
                    task,
                    status,
                    final_answer,
                    touched_files,
                    error,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            error = str(exc)
            patch_path.write_text("", encoding="utf-8")
            artifact_path.write_text(
                _render_worker_artifact(
                    task,
                    "failed",
                    final_answer,
                    touched_files,
                    error,
                ),
                encoding="utf-8",
            )
            if not trace_path.exists():
                trace_path.write_text(
                    json.dumps({"error": error}, indent=2),
                    encoding="utf-8",
                )
        finally:
            try:
                manifest_path = environment.write_manifest(worker_dir)
            finally:
                with self._git_lock:
                    environment.cleanup()

        return LiveSubagentResult(
            task_id=task.id,
            status=status,
            final_answer=final_answer,
            touched_files=touched_files,
            workspace=str(active_workspace),
            trace_path=str(trace_path),
            usage_path=str(worker_dir / "usage.json"),
            patch_path=str(patch_path),
            patch_sha256=patch_sha256,
            artifact_path=str(artifact_path),
            environment_manifest_path=str(manifest_path),
            batch_index=batch_index,
            error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
            usage_summary=usage_summary,
        )

    def run_finalizer(
        self,
        goal: str,
        results: list[LiveSubagentResult],
    ) -> FinalizerResult:
        """在独立只读 worktree 中验证集成 candidate patch。"""

        final_dir = self.root / "finalizer"
        final_dir.mkdir(parents=True, exist_ok=True)
        trace_path = final_dir / "trace.json"
        final_trace = TraceRecorder(str(trace_path))
        usage_path = final_dir / "usage.json"
        environment = ExecutionEnvironment(
            ExecutionEnvironmentConfig(
                mode="worktree",
                workspace=str(self.workspace),
                run_id=f"{self.run_id[:8]}-finalizer",
                network_policy="deny",
                keep_worktree=False,
            )
        )
        answer = ""
        decision = "BLOCKED"
        usage_summary: dict[str, object] = {}
        candidate_snapshot = ""
        try:
            with self._git_lock:
                environment.prepare()
            workspace = environment.active_workspace
            integration_patch = collect_workspace_diff(self.workspace)
            if integration_patch.strip():
                ok, detail = apply_patch_to_workspace(
                    workspace,
                    integration_patch,
                    check_only=False,
                )
                if not ok:
                    raise RuntimeError(
                        f"could not seed integration patch into finalizer: {detail}"
                    )
            candidate_snapshot = collect_workspace_diff(workspace)
            full_registry = self.registry_factory(workspace, environment)
            registry = ToolRegistry()
            for name in sorted(FINALIZER_READ_TOOLS):
                tool = full_registry.get(name)
                if tool is not None:
                    registry.register(tool)
            config = replace(
                self.base_config,
                workspace=str(workspace),
                execution_environment=environment,
                approval_mode="dry-run",
                max_steps=min(self.base_config.max_steps, 8),
                task_state_root=str(final_dir / "task_state"),
                approval_root=str(final_dir / "approvals"),
                human_input_root=str(final_dir / "human_input"),
                human_thread_id=f"{self.run_id}:finalizer",
                operation_ledger_root=str(final_dir / "operation_ledger"),
            )
            answer = build_agent_loop(
                config,
                final_trace,
                registry,
                self.llm_factory(),
            ).run(
                finalizer_task_prompt(goal, results),
                agent_name="FanoutVerifier",
            )
            decision = _decision(answer)
            if collect_workspace_diff(workspace) != candidate_snapshot:
                decision = "BLOCKED"
                answer = "\n".join(
                    [
                        answer.rstrip(),
                        "",
                        "BLOCKED: finalizer modified its isolated workspace: "
                        f"{collect_changed_files(workspace)}",
                    ]
                )
        except Exception as exc:
            answer = f"BLOCKED\nfinalizer error: {exc}"
            final_trace.add(
                0,
                "FanoutVerifier",
                "finalizer_error",
                success=False,
                error=str(exc),
            )
        finally:
            final_trace.set_run_context(
                task=finalizer_task_prompt(goal, results),
                stop_reason=f"finalizer_{decision.lower()}",
                final_answer=answer,
            )
            final_trace.write()
            try:
                usage_json, _ = write_usage_artifacts(trace_path)
                usage_path = usage_json
                usage = json.loads(usage_json.read_text(encoding="utf-8"))
                usage_summary = dict(usage.get("summary") or {})
            finally:
                try:
                    environment.write_manifest(final_dir)
                finally:
                    with self._git_lock:
                        environment.cleanup()
        (final_dir / "verification.md").write_text(
            answer.strip() + "\n",
            encoding="utf-8",
        )
        return FinalizerResult(
            decision=decision,
            answer=answer,
            trace_path=str(trace_path),
            usage_path=str(usage_path),
            usage_summary=usage_summary,
        )

    def validate_recovery_patches(self, patches: list[tuple[str, str]]) -> str:
        """在临时 worktree 顺序重放 patch，返回一个合并 patch。"""

        validation_dir = self.root / "resume_validation"
        environment = ExecutionEnvironment(
            ExecutionEnvironmentConfig(
                mode="worktree",
                workspace=str(self.workspace),
                run_id=f"{self.run_id[:8]}-resume-check",
                network_policy="deny",
                keep_worktree=False,
            )
        )
        try:
            with self._git_lock:
                environment.prepare()
            for task_id, patch in patches:
                ok, detail = apply_patch_to_workspace(
                    environment.active_workspace,
                    patch,
                    check_only=False,
                )
                if not ok:
                    raise RuntimeError(
                        f"fanout resume patch failed for {task_id}: {detail}"
                    )
            return collect_workspace_diff(environment.active_workspace)
        finally:
            try:
                environment.write_manifest(validation_dir)
            finally:
                with self._git_lock:
                    environment.cleanup()


def _filtered_registry(
    full_registry: ToolRegistry,
    task: SubagentTask,
) -> ToolRegistry:
    allowed = (
        set(task.allowed_tools)
        if task.allowed_tools
        else (WRITE_TOOLS if task.write_scope else READ_TOOLS)
    )
    unknown = sorted(name for name in allowed if full_registry.get(name) is None)
    if unknown:
        raise ValueError(
            f"fanout task {task.id} requested unknown tools: {', '.join(unknown)}"
        )
    if not task.write_scope and allowed - READ_TOOLS:
        raise ValueError(
            f"read-only fanout task {task.id} requested write-capable tools"
        )
    registry = ToolRegistry()
    for name in sorted(allowed):
        tool = full_registry.get(name)
        if tool is None:
            raise ValueError(f"fanout task requested unavailable tool: {name}")
        registry.register(tool)
    return registry


def worker_task_prompt(goal: str, task: SubagentTask) -> str:
    """构造隔离 worker 的最小任务上下文。"""

    return "\n".join(
        [
            "You are an isolated worker in a coordinator-driven fanout run.",
            f"task_id={task.id}",
            f"Fanout goal: {goal}",
            f"Worker task: {task.task}",
            f"Declared write scope: {task.write_scope or 'read-only'}",
            f"Expected artifact: {task.expected_artifact}",
            "Implement only this task. Do not touch paths outside the declared scope.",
            "Return a concise evidence-grounded result after using the available tools.",
        ]
    )


def finalizer_task_prompt(
    goal: str,
    results: list[LiveSubagentResult],
) -> str:
    """构造只读 finalizer 的证据输入。"""

    rows = [
        (
            f"- {result.task_id}: {result.status}; touched={result.touched_files}; "
            f"artifact={sanitize_quoted_evidence(result.artifact_path)}; "
            f"output={sanitize_quoted_evidence(result.final_answer[:1200])}"
        )
        for result in results
    ]
    return "\n".join(
        [
            "You are FanoutVerifier, the final read-only integration verifier.",
            f"Goal: {goal}",
            "Use worker outputs as primary evidence. Inspect git_status/git_diff once when needed.",
            "Run diagnostics only when an integrated code patch needs a focused check.",
            "Do not explore unrelated files. Use at most two tool-call rounds.",
            "Then start the answer with PASS, NEEDS_REVISION, or BLOCKED. Do not modify files.",
            "Worker results:",
            *rows,
        ]
    )


def _worker_status(
    task: SubagentTask,
    final_answer: str,
    touched_files: list[str],
) -> tuple[str, str]:
    if final_answer.startswith("waiting_human:"):
        return "waiting_human", ""
    if final_answer.startswith("blocked:"):
        return "blocked", ""
    if not _within_scopes(touched_files, task.write_scope):
        return (
            "scope_violation",
            f"actual touched files escaped declared scope: {touched_files}",
        )
    if not task.write_scope and touched_files:
        return "scope_violation", f"read-only task modified files: {touched_files}"
    return "completed", ""


def _render_worker_artifact(
    task: SubagentTask,
    status: str,
    answer: str,
    touched_files: list[str],
    error: str,
) -> str:
    return "\n".join(
        [
            f"# Subagent {task.id}",
            "",
            f"- status: `{status}`",
            f"- write_scope: `{task.write_scope}`",
            f"- touched_files: `{touched_files}`",
            f"- error: `{error}`",
            "",
            "## Output",
            "",
            answer.strip() or "(no final answer)",
            "",
        ]
    )


def _within_scopes(paths: list[str], scopes: list[str]) -> bool:
    if not paths:
        return True
    if not scopes:
        return False
    for path in paths:
        normalized = path.strip("/")
        if not any(
            normalized == scope.rstrip("/")
            or normalized.startswith(f"{scope.rstrip('/')}/")
            for scope in scopes
        ):
            return False
    return True


def _decision(answer: str) -> str:
    for line in (answer or "").splitlines()[:12]:
        normalized = line.strip().strip("*#:- `").upper()
        for marker in ("PASS", "NEEDS_REVISION", "BLOCKED"):
            if normalized.startswith(marker) or f"VERDICT: {marker}" in normalized:
                return marker
    return "NEEDS_REVISION"

_finalizer_task = finalizer_task_prompt
