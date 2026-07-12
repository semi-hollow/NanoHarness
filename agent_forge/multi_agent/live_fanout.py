from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from agent_forge.observability.trace import TraceRecorder
from agent_forge.observability.usage_report import write_usage_artifacts
from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.execution_environment import ExecutionEnvironment, ExecutionEnvironmentConfig
from agent_forge.runtime.llm_client import LLMClient
from agent_forge.runtime.git_workspace import (
    collect_changed_files,
    collect_workspace_diff,
    collect_workspace_status,
)
from agent_forge.safety.guardrails import sanitize_quoted_evidence
from agent_forge.tools.registry import ToolRegistry

from .fanout import (
    FanoutConflict,
    SubagentResult,
    SubagentTask,
    build_conflict_free_batches,
    build_execution_batches,
    detect_result_conflicts,
)


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
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class FanoutPlan:
    """Validated task DAG consumed by the live fanout coordinator."""

    goal: str
    tasks: list[SubagentTask]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "FanoutPlan":
        goal = str(data.get("goal") or "").strip()
        if not goal:
            raise ValueError("fanout plan goal must not be empty")
        rows = data.get("tasks")
        if not isinstance(rows, list) or not rows:
            raise ValueError("fanout plan tasks must be a non-empty list")
        if len(rows) > 16:
            raise ValueError("fanout plan supports at most 16 tasks")
        tasks: list[SubagentTask] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("fanout task must be an object")
            task_id = str(row.get("id") or "").strip()
            task_text = str(row.get("task") or "").strip()
            if task_id in {".", ".."} or not TASK_ID_PATTERN.fullmatch(task_id):
                raise ValueError(f"invalid fanout task id: {task_id!r}")
            if not task_text:
                raise ValueError(f"fanout task {task_id!r} has no task text")
            max_steps = row.get("max_steps", 12)
            if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 2 <= max_steps <= 32:
                raise ValueError(
                    f"fanout task {task_id!r} max_steps must be an integer from 2 to 32"
                )
            expected_artifact = str(row.get("expected_artifact") or "task_output").strip()
            if (
                expected_artifact in {"", ".", ".."}
                or not TASK_ID_PATTERN.fullmatch(expected_artifact)
            ):
                raise ValueError(
                    f"fanout task {task_id!r} expected_artifact must be a safe file name"
                )
            scopes = [_normalize_scope(value) for value in _string_list(row, "write_scope")]
            tasks.append(
                SubagentTask(
                    id=task_id,
                    task=task_text,
                    depends_on=_string_list(row, "depends_on"),
                    write_scope=scopes,
                    allowed_tools=_string_list(row, "allowed_tools"),
                    expected_artifact=expected_artifact,
                    max_steps=max_steps,
                )
            )
        build_execution_batches(tasks)  # validates ids, dependencies, and cycles
        return cls(goal=goal, tasks=tasks)

    @classmethod
    def load(cls, path: str | Path) -> "FanoutPlan":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("fanout plan JSON must contain an object")
        return cls.from_mapping(data)

    @property
    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "tasks": [
                {
                    "id": task.id,
                    "task": task.task,
                    "depends_on": task.depends_on,
                    "write_scope": task.write_scope,
                    "allowed_tools": task.allowed_tools,
                    "expected_artifact": task.expected_artifact,
                    "max_steps": task.max_steps,
                }
                for task in self.tasks
            ],
        }


@dataclass
class LiveSubagentResult:
    task_id: str
    status: str
    final_answer: str = ""
    touched_files: list[str] = field(default_factory=list)
    workspace: str = ""
    trace_path: str = ""
    usage_path: str = ""
    patch_path: str = ""
    patch_sha256: str = ""
    artifact_path: str = ""
    environment_manifest_path: str = ""
    batch_index: int = 0
    error: str = ""
    duration_ms: int = 0
    usage_summary: dict[str, Any] = field(default_factory=dict)
    resumed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LiveFanoutSummary:
    run_id: str
    goal: str
    status: str
    plan_digest: str
    base_head: str
    batches: list[list[str]]
    results: list[LiveSubagentResult]
    merged_task_ids: list[str]
    conflicts: list[FanoutConflict]
    wall_time_ms: int
    metrics: dict[str, Any]
    final_decision: str = ""
    final_answer: str = ""
    finalizer_trace_path: str = ""
    finalizer_usage_path: str = ""
    finalizer_usage_summary: dict[str, Any] = field(default_factory=dict)
    summary_path: str = ""
    report_path: str = ""
    integration_patch_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "status": self.status,
            "plan_digest": self.plan_digest,
            "base_head": self.base_head,
            "batches": self.batches,
            "results": [result.to_dict() for result in self.results],
            "merged_task_ids": self.merged_task_ids,
            "conflicts": [asdict(conflict) for conflict in self.conflicts],
            "wall_time_ms": self.wall_time_ms,
            "metrics": self.metrics,
            "final_decision": self.final_decision,
            "final_answer": self.final_answer,
            "finalizer_trace_path": self.finalizer_trace_path,
            "finalizer_usage_path": self.finalizer_usage_path,
            "finalizer_usage_summary": self.finalizer_usage_summary,
            "summary_path": self.summary_path,
            "report_path": self.report_path,
            "integration_patch_path": self.integration_patch_path,
        }


RegistryFactory = Callable[[Path, ExecutionEnvironment], ToolRegistry]
LLMFactory = Callable[[], LLMClient]


class LiveFanoutCoordinator:
    """Run structured subagent tasks through real isolated AgentLoop workers."""

    def __init__(
        self,
        *,
        plan: FanoutPlan,
        base_config: RuntimeConfig,
        trace: TraceRecorder,
        run_dir: str | Path,
        llm_factory: LLMFactory,
        registry_factory: RegistryFactory,
        max_workers: int = 4,
        resume_from: str | Path | None = None,
    ) -> None:
        self.plan = plan
        self.base_config = base_config
        self.trace = trace
        self.run_dir = Path(run_dir).resolve()
        self.root = self.run_dir / "fanout"
        self.root.mkdir(parents=True, exist_ok=True)
        self.llm_factory = llm_factory
        self.registry_factory = registry_factory
        self.max_workers = max(1, min(int(max_workers), 8))
        self.resume_from = Path(resume_from) if resume_from else None
        self.workspace = Path(base_config.workspace).resolve()
        self._git_lock = threading.Lock()
        self._base_head = ""

    def run(self) -> LiveFanoutSummary:
        started = time.monotonic()
        base_head = _git_output(self.workspace, ["rev-parse", "HEAD"])
        if not base_head:
            raise RuntimeError("live fanout requires a git workspace")
        self._base_head = base_head
        has_write_tasks = any(task.write_scope for task in self.plan.tasks)
        if has_write_tasks and not getattr(self.base_config, "auto_approve_writes", True):
            raise RuntimeError(
                "live fanout manual write approval is not recoverable across ephemeral worktrees; "
                "use single/multi mode for per-operation approval"
            )
        if has_write_tasks and _git_status(self.workspace):
            raise RuntimeError("write fanout requires a clean integration workspace")

        batches = build_conflict_free_batches(self.plan.tasks)
        batch_ids = [[task.id for task in batch] for batch in batches]
        results: list[LiveSubagentResult] = []
        merged_task_ids: list[str] = []
        successful_ids: set[str] = set()
        conflicts: list[FanoutConflict] = []
        self.trace.add(0, "LiveFanoutCoordinator", "fanout_start", plan=self.plan.to_dict(), batches=batch_ids)

        if self.resume_from:
            recovered = self._restore_previous(base_head)
            results.extend(recovered)
            successful_ids.update(result.task_id for result in recovered)
            merged_task_ids.extend(result.task_id for result in recovered)
        _write_json_atomic(self.root / "fanout_plan.json", self.plan.to_dict())
        self._write_checkpoint(base_head, results, merged_task_ids, status="running")

        for batch_index, batch in enumerate(batches):
            pending = [task for task in batch if task.id not in successful_ids]
            runnable: list[SubagentTask] = []
            for task in pending:
                if set(task.depends_on).issubset(successful_ids):
                    runnable.append(task)
                else:
                    results.append(
                        LiveSubagentResult(
                            task_id=task.id,
                            status="blocked_dependency",
                            batch_index=batch_index,
                            error="one or more dependencies did not complete",
                        )
                    )
            if not runnable:
                self._write_checkpoint(base_head, results, merged_task_ids, status="running")
                continue

            base_patch = collect_workspace_diff(self.workspace)
            batch_results = self._run_batch(runnable, batch_index, base_patch)
            dynamic_conflicts = detect_result_conflicts(
                [
                    SubagentResult(
                        task_id=result.task_id,
                        status=result.status,
                        touched_files=result.touched_files,
                        batch_index=batch_index,
                    )
                    for result in batch_results
                    if result.status == "completed"
                ]
            )
            if dynamic_conflicts:
                conflict_ids = {task_id for conflict in dynamic_conflicts for task_id in conflict.task_ids}
                for result in batch_results:
                    if result.task_id in conflict_ids:
                        result.status = "dynamic_conflict"
                conflicts.extend(dynamic_conflicts)

            by_id = {task.id: task for task in runnable}
            for result in batch_results:
                task = by_id[result.task_id]
                if result.status != "completed":
                    continue
                if task.write_scope:
                    patch = Path(result.patch_path).read_text(encoding="utf-8") if result.patch_path else ""
                    if not patch.strip():
                        result.status = "no_patch"
                        continue
                    ok, detail = _apply_patch(self.workspace, patch, check_only=True)
                    if not ok:
                        result.status = "merge_conflict"
                        result.error = detail
                        conflicts.append(FanoutConflict([result.task_id], f"patch apply check failed: {detail}"))
                        continue
                    ok, detail = _apply_patch(self.workspace, patch, check_only=False)
                    if not ok:
                        result.status = "merge_conflict"
                        result.error = detail
                        conflicts.append(FanoutConflict([result.task_id], f"patch apply failed: {detail}"))
                        continue
                successful_ids.add(result.task_id)
                merged_task_ids.append(result.task_id)
            results.extend(batch_results)
            self._write_checkpoint(base_head, results, merged_task_ids, status="running")
            self.trace.add(
                batch_index + 1,
                "LiveFanoutCoordinator",
                "fanout_batch_done",
                batch=[task.id for task in runnable],
                results=[result.to_dict() for result in batch_results],
                conflicts=[asdict(conflict) for conflict in dynamic_conflicts],
            )

        integration_patch_path = self.root / "integration.patch"
        integration_patch_path.write_text(collect_workspace_diff(self.workspace), encoding="utf-8")
        all_successful = len(successful_ids) == len(self.plan.tasks)
        final_decision = ""
        final_answer = ""
        finalizer_trace_path = ""
        finalizer_usage_path = ""
        finalizer_usage_summary: dict[str, Any] = {}
        if all_successful and not conflicts:
            (
                final_decision,
                final_answer,
                finalizer_trace_path,
                finalizer_usage_path,
                finalizer_usage_summary,
            ) = self._run_finalizer(results)

        if conflicts or any(result.status in {"scope_violation", "dynamic_conflict", "merge_conflict"} for result in results):
            status = "conflict_resolution_required"
        elif not all_successful:
            status = "partial_failure"
        elif final_decision == "PASS":
            status = "passed"
        else:
            status = "needs_revision"

        wall_time_ms = int((time.monotonic() - started) * 1000)
        summary = LiveFanoutSummary(
            run_id=self.trace.run_id,
            goal=self.plan.goal,
            status=status,
            plan_digest=self.plan.digest,
            base_head=base_head,
            batches=batch_ids,
            results=results,
            merged_task_ids=merged_task_ids,
            conflicts=conflicts,
            wall_time_ms=wall_time_ms,
            metrics=_aggregate_metrics(
                results,
                wall_time_ms,
                max_workers=self.max_workers,
                finalizer_usage=finalizer_usage_summary,
            ),
            final_decision=final_decision,
            final_answer=final_answer,
            finalizer_trace_path=finalizer_trace_path,
            finalizer_usage_path=finalizer_usage_path,
            finalizer_usage_summary=finalizer_usage_summary,
            integration_patch_path=str(integration_patch_path),
        )
        self._write_checkpoint(base_head, results, merged_task_ids, status=status)
        self._write_summary(summary)
        self.trace.add(
            len(batches) + 2,
            "LiveFanoutCoordinator",
            "fanout_done",
            success=status == "passed",
            status=status,
            metrics=summary.metrics,
        )
        return summary

    def _run_batch(
        self,
        tasks: list[SubagentTask],
        batch_index: int,
        base_patch: str,
    ) -> list[LiveSubagentResult]:
        results: dict[str, LiveSubagentResult] = {}
        workers = max(1, min(self.max_workers, len(tasks)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self._run_worker, task, batch_index, base_patch): task
                for task in tasks
            }
            for future in as_completed(futures):
                task = futures[future]
                try:
                    results[task.id] = future.result()
                except Exception as exc:
                    results[task.id] = LiveSubagentResult(
                        task_id=task.id,
                        status="failed",
                        batch_index=batch_index,
                        error=str(exc),
                    )
        return [results[task.id] for task in tasks]

    def _run_worker(self, task: SubagentTask, batch_index: int, base_patch: str) -> LiveSubagentResult:
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
                run_id=f"{self.trace.run_id[:8]}-{task.id}",
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
        usage_summary: dict[str, Any] = {}
        patch_sha256 = ""
        try:
            with self._git_lock:
                environment.prepare()
            active_workspace = environment.active_workspace
            if base_patch.strip():
                ok, detail = _apply_patch(active_workspace, base_patch, check_only=False)
                if not ok:
                    raise RuntimeError(f"could not seed integrated patch into worker: {detail}")
                _commit_worker_baseline(active_workspace)

            full_registry = self.registry_factory(active_workspace, environment)
            registry = _filtered_registry(full_registry, task)
            worker_trace = TraceRecorder(str(trace_path))
            worker_config = replace(
                self.base_config,
                workspace=str(active_workspace),
                execution_environment=environment,
                max_steps=min(getattr(self.base_config, "max_steps", 12), task.max_steps),
                approval_mode="dry-run" if not task.write_scope else getattr(self.base_config, "approval_mode", "trusted"),
                task_state_root=str(worker_dir / "task_state"),
                approval_root=str(worker_dir / "approvals"),
                human_input_root=getattr(
                    self.base_config,
                    "human_input_root",
                    ".agent_forge/human_input",
                ),
                human_thread_id=(
                    f"fanout:{self.plan.digest[:16]}:{self._base_head[:12]}:{task.id}"
                ),
                operation_ledger_root=str(worker_dir / "operation_ledger"),
            )
            final_answer = AgentLoop(worker_config, worker_trace, registry, self.llm_factory()).run(
                _worker_task(self.plan.goal, task),
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
            if final_answer.startswith("waiting_human:"):
                status = "waiting_human"
            elif final_answer.startswith("blocked:"):
                status = "blocked"
            elif not _within_scopes(touched_files, task.write_scope):
                status = "scope_violation"
                error = f"actual touched files escaped declared scope: {touched_files}"
            elif not task.write_scope and touched_files:
                status = "scope_violation"
                error = f"read-only task modified files: {touched_files}"
            else:
                status = "completed"
            artifact_path.write_text(
                _render_worker_artifact(task, status, final_answer, touched_files, error),
                encoding="utf-8",
            )
        except Exception as exc:
            error = str(exc)
            patch_path.write_text("", encoding="utf-8")
            artifact_path.write_text(
                _render_worker_artifact(task, "failed", final_answer, touched_files, error),
                encoding="utf-8",
            )
            if not trace_path.exists():
                trace_path.write_text(json.dumps({"error": error}, indent=2), encoding="utf-8")
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

    def _run_finalizer(
        self,
        results: list[LiveSubagentResult],
    ) -> tuple[str, str, str, str, dict[str, Any]]:
        final_dir = self.root / "finalizer"
        final_dir.mkdir(parents=True, exist_ok=True)
        trace_path = final_dir / "trace.json"
        final_trace = TraceRecorder(str(trace_path))
        usage_path = final_dir / "usage.json"
        environment = ExecutionEnvironment(
            ExecutionEnvironmentConfig(
                mode="worktree",
                workspace=str(self.workspace),
                run_id=f"{self.trace.run_id[:8]}-finalizer",
                network_policy="deny",
                keep_worktree=False,
            )
        )
        answer = ""
        decision = "BLOCKED"
        usage_summary: dict[str, Any] = {}
        candidate_snapshot = ""
        try:
            with self._git_lock:
                environment.prepare()
            workspace = environment.active_workspace
            integration_patch = collect_workspace_diff(self.workspace)
            if integration_patch.strip():
                ok, detail = _apply_patch(workspace, integration_patch, check_only=False)
                if not ok:
                    raise RuntimeError(f"could not seed integration patch into finalizer: {detail}")
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
                max_steps=min(getattr(self.base_config, "max_steps", 12), 8),
                task_state_root=str(final_dir / "task_state"),
                approval_root=str(final_dir / "approvals"),
                human_input_root=str(final_dir / "human_input"),
                human_thread_id=f"{self.trace.run_id}:finalizer",
                operation_ledger_root=str(final_dir / "operation_ledger"),
            )
            answer = AgentLoop(config, final_trace, registry, self.llm_factory()).run(
                _finalizer_task(self.plan.goal, results),
                agent_name="FanoutVerifier",
            )
            decision = _decision(answer)
            post_run_snapshot = collect_workspace_diff(workspace)
            if post_run_snapshot != candidate_snapshot:
                verifier_changes = collect_changed_files(workspace)
                decision = "BLOCKED"
                answer = "\n".join(
                    [
                        answer.rstrip(),
                        "",
                        f"BLOCKED: finalizer modified its isolated workspace: {verifier_changes}",
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
                task=_finalizer_task(self.plan.goal, results),
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
        (final_dir / "verification.md").write_text(answer.strip() + "\n", encoding="utf-8")
        return decision, answer, str(trace_path), str(usage_path), usage_summary

    def _restore_previous(self, base_head: str) -> list[LiveSubagentResult]:
        resume_path = self.resume_from
        if resume_path is None:
            return []
        summary_path = _resolve_resume_artifact(resume_path)
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        if data.get("plan_digest") != self.plan.digest:
            raise RuntimeError("fanout resume plan digest does not match")
        if data.get("base_head") != base_head:
            raise RuntimeError("fanout resume base commit does not match")
        by_id = {str(item.get("task_id")): item for item in data.get("results", []) if isinstance(item, dict)}
        merged_ids = set(data.get("merged_task_ids") or [])
        unknown_ids = sorted(merged_ids - {task.id for task in self.plan.tasks})
        if unknown_ids:
            raise RuntimeError(f"fanout resume contains unknown merged tasks: {', '.join(unknown_ids)}")
        prepared: list[tuple[SubagentTask, LiveSubagentResult, str]] = []
        for task in self.plan.tasks:
            if task.id not in merged_ids:
                continue
            item = by_id.get(task.id)
            if not item:
                raise RuntimeError(f"fanout resume has no result for merged task: {task.id}")
            if item.get("status") != "completed":
                raise RuntimeError(f"fanout resume merged task is not completed: {task.id}")
            restored_item = dict(item)
            restored_item["resumed"] = True
            result = LiveSubagentResult(**restored_item)
            patch = ""
            patch_path = Path(str(item.get("patch_path") or ""))
            if task.write_scope:
                if not patch_path.exists():
                    raise RuntimeError(f"fanout resume patch is missing: {patch_path}")
                patch = patch_path.read_text(encoding="utf-8")
                expected_digest = str(item.get("patch_sha256") or "")
                actual_digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
                if not expected_digest or actual_digest != expected_digest:
                    raise RuntimeError(f"fanout resume patch digest does not match for {task.id}")
            prepared.append((task, result, patch))

        patches = [(task.id, patch) for task, _, patch in prepared if patch]
        if patches:
            combined_patch = self._validate_recovery_patches(patches)
            ok, detail = _apply_patch(self.workspace, combined_patch, check_only=True)
            if not ok:
                raise RuntimeError(f"fanout resume integration check failed: {detail}")
            ok, detail = _apply_patch(self.workspace, combined_patch, check_only=False)
            if not ok:
                raise RuntimeError(f"fanout resume integration failed: {detail}")
        return [result for _, result, _ in prepared]

    def _validate_recovery_patches(self, patches: list[tuple[str, str]]) -> str:
        """Replay all recovered patches off-workspace before one final apply."""

        validation_dir = self.root / "resume_validation"
        environment = ExecutionEnvironment(
            ExecutionEnvironmentConfig(
                mode="worktree",
                workspace=str(self.workspace),
                run_id=f"{self.trace.run_id[:8]}-resume-check",
                network_policy="deny",
                keep_worktree=False,
            )
        )
        try:
            with self._git_lock:
                environment.prepare()
            for task_id, patch in patches:
                ok, detail = _apply_patch(environment.active_workspace, patch, check_only=False)
                if not ok:
                    raise RuntimeError(f"fanout resume patch failed for {task_id}: {detail}")
            return collect_workspace_diff(environment.active_workspace)
        finally:
            try:
                environment.write_manifest(validation_dir)
            finally:
                with self._git_lock:
                    environment.cleanup()

    def _write_checkpoint(
        self,
        base_head: str,
        results: list[LiveSubagentResult],
        merged_task_ids: list[str],
        *,
        status: str,
    ) -> None:
        _write_json_atomic(
            self.root / "fanout_checkpoint.json",
            {
                "schema_version": 1,
                "status": status,
                "plan_digest": self.plan.digest,
                "base_head": base_head,
                "merged_task_ids": list(merged_task_ids),
                "results": [result.to_dict() for result in results],
                "updated_at": time.time(),
            },
        )

    def _write_summary(self, summary: LiveFanoutSummary) -> None:
        summary_path = self.root / "fanout_summary.json"
        report_path = self.root / "fanout_report.md"
        summary.summary_path = str(summary_path)
        summary.report_path = str(report_path)
        summary_path.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.write_text(_render_report(summary), encoding="utf-8")


def _normalize_scope(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"write scope must be a relative workspace path: {value!r}")
    normalized = path.as_posix()
    if not normalized or normalized == ".":
        raise ValueError(f"write scope must be a relative workspace path: {value!r}")
    return normalized.rstrip("/") + ("/" if text.endswith("/") else "")


def _string_list(data: dict[str, Any], key: str) -> list[str]:
    values = data.get(key)
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError(f"fanout task {key} must be a list")
    normalized = [str(value).strip() for value in values]
    if any(not value for value in normalized):
        raise ValueError(f"fanout task {key} entries must not be empty")
    return list(dict.fromkeys(normalized))


def _resolve_resume_artifact(path: Path) -> Path:
    if path.is_file():
        return path
    roots = [path / "fanout", path]
    for filename in ("fanout_summary.json", "fanout_checkpoint.json"):
        for root in roots:
            candidate = root / filename
            if candidate.exists():
                return candidate
    raise FileNotFoundError(f"no fanout summary or checkpoint found under {path}")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _filtered_registry(full_registry: ToolRegistry, task: SubagentTask) -> ToolRegistry:
    allowed = set(task.allowed_tools) if task.allowed_tools else (WRITE_TOOLS if task.write_scope else READ_TOOLS)
    unknown = sorted(name for name in allowed if full_registry.get(name) is None)
    if unknown:
        raise ValueError(f"fanout task {task.id} requested unknown tools: {', '.join(unknown)}")
    if not task.write_scope and allowed - READ_TOOLS:
        raise ValueError(f"read-only fanout task {task.id} requested write-capable tools")
    registry = ToolRegistry()
    for name in sorted(allowed):
            tool = full_registry.get(name)
            if tool is None:
                raise ValueError(f"fanout task requested unavailable tool: {name}")
            registry.register(tool)
    return registry


def _worker_task(goal: str, task: SubagentTask) -> str:
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


def _finalizer_task(goal: str, results: list[LiveSubagentResult]) -> str:
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


def _git_output(workspace: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_status(workspace: Path) -> str:
    return "\n".join(collect_workspace_status(workspace))


def _apply_patch(workspace: Path, patch: str, *, check_only: bool) -> tuple[bool, str]:
    command = ["git", "apply", "--binary"]
    if check_only:
        command.append("--check")
    result = subprocess.run(
        command,
        cwd=workspace,
        input=patch,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def _commit_worker_baseline(workspace: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Agent Forge",
            "-c",
            "user.email=agent-forge@local",
            "commit",
            "-m",
            "fanout integrated baseline",
        ],
        cwd=workspace,
        check=True,
        capture_output=True,
    )


def _aggregate_metrics(
    results: list[LiveSubagentResult],
    wall_time_ms: int,
    *,
    max_workers: int,
    finalizer_usage: dict[str, Any],
) -> dict[str, Any]:
    keys = ("llm_calls", "total_tokens", "estimated_cost_usd", "llm_latency_ms", "tool_calls", "failed_tool_calls")
    current_worker_duration_ms = sum(result.duration_ms for result in results if not result.resumed)
    resumed_worker_duration_ms = sum(result.duration_ms for result in results if result.resumed)
    metrics: dict[str, Any] = {
        "task_count": len(results),
        "completed_count": sum(result.status == "completed" for result in results),
        "resumed_count": sum(result.resumed for result in results),
        "max_workers": max_workers,
        "wall_time_ms": wall_time_ms,
        "summed_worker_duration_ms": sum(result.duration_ms for result in results),
        "current_worker_duration_ms": current_worker_duration_ms,
        "resumed_worker_duration_ms": resumed_worker_duration_ms,
        "worker_time_to_wall_ratio": round(
            current_worker_duration_ms / wall_time_ms,
            4,
        )
        if wall_time_ms
        else 0.0,
    }
    for key in keys:
        current_worker_value = sum(
            float(result.usage_summary.get(key) or 0) for result in results if not result.resumed
        )
        resumed_worker_value = sum(
            float(result.usage_summary.get(key) or 0) for result in results if result.resumed
        )
        current_value = current_worker_value + float(finalizer_usage.get(key) or 0)
        evidence_chain_value = current_value + resumed_worker_value
        if key == "estimated_cost_usd":
            metrics[key] = round(current_value, 6)
            metrics[f"resumed_{key}"] = round(resumed_worker_value, 6)
            metrics[f"evidence_chain_{key}"] = round(evidence_chain_value, 6)
        else:
            metrics[key] = int(current_value)
            metrics[f"resumed_{key}"] = int(resumed_worker_value)
            metrics[f"evidence_chain_{key}"] = int(evidence_chain_value)
    metrics["finalizer_llm_calls"] = int(finalizer_usage.get("llm_calls") or 0)
    return metrics


def _render_report(summary: LiveFanoutSummary) -> str:
    current_metric_keys = (
        "task_count",
        "completed_count",
        "max_workers",
        "wall_time_ms",
        "current_worker_duration_ms",
        "worker_time_to_wall_ratio",
        "llm_calls",
        "total_tokens",
        "estimated_cost_usd",
        "tool_calls",
        "failed_tool_calls",
        "finalizer_llm_calls",
    )
    recovery_metric_keys = (
        "resumed_count",
        "resumed_worker_duration_ms",
        "resumed_llm_calls",
        "resumed_total_tokens",
        "resumed_estimated_cost_usd",
        "evidence_chain_llm_calls",
        "evidence_chain_total_tokens",
        "evidence_chain_estimated_cost_usd",
    )
    lines = [
        "# Live Fanout Report",
        "",
        "## Run",
        "",
        f"- run_id: `{summary.run_id}`",
        f"- status: `{summary.status}`",
        f"- goal: {summary.goal}",
        f"- base_head: `{summary.base_head}`",
        f"- plan_digest: `{summary.plan_digest}`",
        f"- batches: `{summary.batches}`",
        f"- merged_task_ids: `{summary.merged_task_ids}`",
        f"- final_decision: `{summary.final_decision or 'not_run'}`",
        "",
        "## Current Run Metrics",
        "",
        "| metric | value |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {key} | {summary.metrics.get(key, 0)} |" for key in current_metric_keys)
    lines.extend(
        [
            "",
            "## Recovery Accounting",
            "",
            "Recovered usage is historical; evidence-chain totals combine it with this run.",
            "",
            "| metric | value |",
            "| --- | ---: |",
        ]
    )
    lines.extend(f"| {key} | {summary.metrics.get(key, 0)} |" for key in recovery_metric_keys)
    lines.extend(
        [
            "",
            "## Tasks",
            "",
            "| task | status | batch | resumed | touched files | patch | trace |",
            "| --- | --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for result in summary.results:
        lines.append(
            f"| `{result.task_id}` | `{result.status}` | {result.batch_index} | "
            f"`{result.resumed}` | `{result.touched_files}` | "
            f"[patch]({result.patch_path}) | [trace]({result.trace_path}) |"
        )
    lines.extend(["", "## Conflict Gate", ""])
    if summary.conflicts:
        lines.extend(f"- `{conflict.task_ids}`: {conflict.reason}" for conflict in summary.conflicts)
    else:
        lines.append("- No static, dynamic, scope, or patch-apply conflict was observed.")
    lines.extend(
        [
            "",
            "## Finalizer",
            "",
            f"- trace: `{summary.finalizer_trace_path or 'not_run'}`",
            f"- usage: `{summary.finalizer_usage_path or 'not_run'}`",
            f"- llm_calls: `{summary.finalizer_usage_summary.get('llm_calls', 0)}`",
            "",
            "## Claim Boundary",
            "",
            "A merged patch and FanoutVerifier PASS are runtime evidence, not official benchmark resolution.",
            "",
        ]
    )
    return "\n".join(lines)
