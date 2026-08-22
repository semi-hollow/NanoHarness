"""基于本地 worktree 和真实 AgentLoop 的 fanout worker adapter。"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

from agent_forge.observability.adapters.json_trace import TraceRecorder
from agent_forge.observability.api import write_usage_artifacts
from agent_forge.runtime.api import build_agent_loop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.adapters.execution_environment import (
    ExecutionEnvironment,
    ExecutionEnvironmentConfig,
)
from agent_forge.runtime.adapters.git_workspace import (
    collect_changed_files,
    collect_workspace_diff,
)
from agent_forge.runtime.ports.model import ModelPort
from agent_forge.safety.guardrails import sanitize_quoted_evidence
from agent_forge.tools.registry import ToolRegistry

from ..domain.fanout import SubagentTask
from ..domain.tool_policy import FINALIZER_READ_TOOLS, READ_TOOLS, WRITE_TOOLS
from ..domain.live import (
    FanoutPlan,
    CriterionResult,
    FinalizerResult,
    LiveSubagentResult,
    WorkerHandoff,
    project_worker_handoff,
)
from ..ports import FanoutWorkerPort
from .git_workspace import apply_unified_diff_to_workspace, commit_worker_baseline

RegistryFactory = Callable[[Path, ExecutionEnvironment], ToolRegistry]
LLMFactory = Callable[[], ModelPort]


class LocalAgentWorkerAdapter(FanoutWorkerPort):
    """执行隔离 worker、只读 finalizer 和恢复 diff 验证。"""

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

    # 主要入口：把一个 SubagentTask 变成隔离运行、候选 Diff 和可合并 Worker 结果。
    def run_worker(
        self,
        task: SubagentTask,
        batch_index: int,
        base_diff_text: str,
        dependency_handoffs: list[WorkerHandoff],
        attempt: int,
    ) -> LiveSubagentResult:
        """在临时 worktree 中运行一个受 scope 限制的 AgentLoop。"""

        # region 1. 输出契约准备（实现细节）：固定目录，并先准备失败兜底值
        # 先创建所有稳定路径和默认失败值，保证后续任意阶段抛异常时仍能返回结构完整的
        # LiveSubagentResult，而不是让 Coordinator 依赖临时 worktree 内部状态。
        started = time.monotonic()
        worker_dir = self.root / "workers" / task.id / f"attempt-{attempt}"
        worker_dir.mkdir(parents=True, exist_ok=True)
        trace_path = worker_dir / "trace.json"
        candidate_diff_path = worker_dir / "candidate_changes.diff"
        artifact_path = worker_dir / f"{task.expected_artifact}.md"
        environment = ExecutionEnvironment(
            ExecutionEnvironmentConfig(
                mode="worktree",
                workspace=str(self.workspace),
                run_id=f"{self.run_id[:8]}-{task.id}-a{attempt}",
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
        candidate_diff_sha256 = ""
        stop_reason = ""
        failure_kind = ""
        retryable = False
        validation_evidence: list[dict[str, object]] = []
        unresolved_issues: list[str] = []
        handoff: WorkerHandoff | None = None
        # endregion 1. 输出契约准备结束
        try:
            # region 2. 隔离工作区（实现细节）：创建 worktree，并带入前置任务的已合并改动
            with self._git_lock:
                environment.prepare()
            active_workspace = environment.active_workspace
            if base_diff_text.strip():
                # 有依赖的后续 Worker 必须看见前序改动；提交为新基线后，自己的 Diff
                # 只包含本任务增量，不会把前序改动重复交给 Coordinator。
                ok, detail = apply_unified_diff_to_workspace(
                    active_workspace,
                    base_diff_text,
                    check_only=False,
                )
                if not ok:
                    raise RuntimeError(
                        f"could not seed integrated diff into worker: {detail}"
                    )
                commit_worker_baseline(active_workspace)
            # endregion 2. 隔离工作区结束

            # region 3. 受限 Runtime 装配（实现细节）：收窄工具，并隔离状态存储
            # Registry 按任务允许工具过滤；RuntimeConfig 的 checkpoint、approval 和操作状态表
            # 全部写入 worker_dir，多个并发 Worker 不会共享可变运行状态。
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
                    "dry-run"
                    if not task.write_scope
                    else self.base_config.approval_mode
                ),
                task_state_root=str(worker_dir / "task_state"),
                approval_root=str(worker_dir / "approvals"),
                human_thread_id=(
                    f"fanout:{self.plan.digest[:16]}:{self.base_head[:12]}:"
                    f"{task.id}:attempt-{attempt}"
                ),
                operation_ledger_root=str(worker_dir / "operation_ledger"),
            )
            # endregion 3. 受限 Runtime 装配结束

            # region 4. AgentLoop 执行（主链）：任务契约进入模型，结果写入独立 Trace/Usage
            # worker_task_prompt 把总目标与子任务 scope 合并；AgentLoop 完成后立即冻结
            # Trace/Usage，后续候选收集只读取 workspace，不修改模型运行证据。
            final_answer = build_agent_loop(
                worker_config,
                worker_trace,
                registry,
                self.llm_factory(),
            ).run(
                worker_task_prompt(
                    self.plan.goal,
                    task,
                    dependency_handoffs,
                ),
                agent_name=f"Subagent:{task.id}",
            )
            worker_trace.write()
            usage_json, _ = write_usage_artifacts(trace_path)
            usage = json.loads(usage_json.read_text(encoding="utf-8"))
            usage_summary = dict(usage.get("summary") or {})
            # endregion 4. AgentLoop 执行结束

            # region 5. 候选结果收集（主链）：提取本任务 Diff，并校验实际改动没有越界
            # touched_files 是动态冲突和 scope_violation 的共同事实来源；即使最终文本声称
            # 完成，_worker_status 也会依据真实文件范围决定是否允许 Coordinator 合并。
            candidate_diff_text = collect_workspace_diff(active_workspace)
            candidate_diff_path.write_text(candidate_diff_text, encoding="utf-8")
            candidate_diff_sha256 = hashlib.sha256(
                candidate_diff_text.encode("utf-8")
            ).hexdigest()
            touched_files = collect_changed_files(active_workspace)
            status, error = _worker_status(task, final_answer, touched_files)
            stop_reason, failure_kind, retryable, validation_evidence = _trace_outcome(
                trace_path
            )
            unresolved_issues = _unresolved_issues(status, error)
            handoff = project_worker_handoff(
                LiveSubagentResult(
                    task_id=task.id,
                    status=status,
                    final_answer=final_answer,
                    touched_files=touched_files,
                    artifact_path=str(artifact_path),
                    error=error,
                    validation_evidence=validation_evidence,
                    unresolved_issues=unresolved_issues,
                )
            )
            artifact_path.write_text(
                _render_worker_artifact(
                    task,
                    status,
                    final_answer,
                    touched_files,
                    error,
                    validation_evidence,
                ),
                encoding="utf-8",
            )
            # endregion 5. 候选结果收集结束
        except Exception as exc:
            # 失败也必须发布结构稳定的 artifact；Coordinator 不需要靠异常猜 Worker 状态。
            error = str(exc)
            stop_reason = "worker_adapter_exception"
            failure_kind = "worker_adapter_exception"
            retryable = False
            validation_evidence = []
            unresolved_issues = [error]
            handoff = project_worker_handoff(
                LiveSubagentResult(
                    task_id=task.id,
                    status="failed",
                    final_answer=final_answer,
                    touched_files=touched_files,
                    artifact_path=str(artifact_path),
                    error=error,
                    validation_evidence=validation_evidence,
                    unresolved_issues=unresolved_issues,
                )
            )
            candidate_diff_path.write_text("", encoding="utf-8")
            artifact_path.write_text(
                _render_worker_artifact(
                    task,
                    "failed",
                    final_answer,
                    touched_files,
                    error,
                    validation_evidence,
                ),
                encoding="utf-8",
            )
            if not trace_path.exists():
                trace_path.write_text(
                    json.dumps({"error": error}, indent=2),
                    encoding="utf-8",
                )
        finally:
            # 无论成功或失败都保留环境清单；临时 worktree 最后统一回收。
            try:
                manifest_path = environment.write_manifest(worker_dir)
            finally:
                with self._git_lock:
                    environment.cleanup()

        # 返回的是 Coordinator 唯一依赖的 Worker 数据契约，不暴露内部 AgentLoop 对象。
        return LiveSubagentResult(
            task_id=task.id,
            status=status,
            final_answer=final_answer,
            touched_files=touched_files,
            workspace=str(active_workspace),
            trace_path=str(trace_path),
            usage_path=str(worker_dir / "usage.json"),
            candidate_diff_path=str(candidate_diff_path),
            candidate_diff_sha256=candidate_diff_sha256,
            artifact_path=str(artifact_path),
            environment_manifest_path=str(manifest_path),
            batch_index=batch_index,
            error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
            usage_summary=usage_summary,
            attempt=attempt,
            stop_reason=stop_reason,
            failure_kind=failure_kind,
            retryable=retryable,
            validation_evidence=validation_evidence,
            unresolved_issues=unresolved_issues,
            handoff=handoff,
        )

    # 主要入口：在独立只读环境复核合并结果，且禁止 Finalizer 产生新改动。
    def run_finalizer(
        self,
        plan: FanoutPlan,
        results: list[LiveSubagentResult],
    ) -> FinalizerResult:
        """把已集成 diff 复制到独立 worktree，并用只读 Runtime 做最终验收。

        Finalizer 只获得检查类 Tool、禁网和 dry-run 权限；只有明确输出 PASS 才通过，
        异常或非 PASS 均保留证据并返回阻断结论，不修改或回滚 Coordinator 的成果。
        """

        # region 1. 验证输出准备（实现细节）：固定 Trace、Usage 和默认阻断结论
        # Finalizer 默认 BLOCKED，只有只读验证明确返回 PASS 才能升级；所有输出路径
        # 预先固定，保证验证基础设施异常也能留下证据。
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
        criterion_results: list[CriterionResult] = []
        # endregion 1. 验证输出准备结束
        try:
            # region 2. 隔离候选结果（实现细节）：把 Coordinator 已合并 Diff 复制到新 worktree
            # Finalizer 不直接进入集成 workspace；先复制当前 integrated diff 到隔离 worktree，
            # 既能验证真实候选，也能在验证器误写时保护主结果。
            with self._git_lock:
                environment.prepare()
            workspace = environment.active_workspace
            integrated_diff_text = collect_workspace_diff(self.workspace)
            if integrated_diff_text.strip():
                ok, detail = apply_unified_diff_to_workspace(
                    workspace,
                    integrated_diff_text,
                    check_only=False,
                )
                if not ok:
                    raise RuntimeError(
                        f"could not seed integrated diff into finalizer: {detail}"
                    )
            candidate_snapshot = collect_workspace_diff(workspace)
            # endregion 2. 隔离候选结果结束

            # region 3. 只读 Runtime 装配（实现细节）：只暴露 Diff、状态和验证工具
            # 从完整 Registry 中只复制 FINALIZER_READ_TOOLS，并使用 dry-run 配置；
            # 工具可见性和执行权限两层都禁止 Finalizer 修补候选代码。
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
            # endregion 3. 只读 Runtime 装配结束

            # region 4. Finalizer 执行与质量门（主链）：解析判定，并检查验证者没有改代码
            # 模型判定后再次比较 workspace diff；任何新增改动都会把 decision 强制降为
            # BLOCKED，防止“验证者顺手修好代码”被误算成 Worker 结果通过。
            answer = build_agent_loop(
                config,
                final_trace,
                registry,
                self.llm_factory(),
            ).run(
                finalizer_task_prompt(plan.goal, results, plan=plan),
                agent_name="FanoutFinalizer",
            )
            decision = _decision(answer)
            criterion_results = _criterion_results(answer, _all_criteria(plan))
            if decision == "PASS" and any(
                result.status != "PASS" for result in criterion_results
            ):
                decision = "NEEDS_REVISION"
            if decision == "PASS" and _has_failed_runtime_evidence(plan, results):
                decision = "NEEDS_REVISION"
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
            # endregion 4. Finalizer 执行与质量门结束
        except Exception as exc:
            # 验证基础设施异常按 BLOCKED 处理，不能冒充 PASS。
            answer = f"BLOCKED\nfinalizer error: {exc}"
            final_trace.add(
                0,
                "FanoutFinalizer",
                "finalizer_error",
                success=False,
                error=str(exc),
            )
        finally:
            # 即使异常也写 Trace/Usage/环境清单，随后回收验证 worktree。
            final_trace.set_run_context(
                task=finalizer_task_prompt(plan.goal, results, plan=plan),
                stop_reason=f"finalizer_{decision.lower()}",
                stop_output=answer,
                final_answer=answer if decision == "PASS" else None,
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
        # verification.md 是给人看的结论；FinalizerResult 是给 Coordinator 的结构化结果。
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
            criterion_results=criterion_results,
        )

    def validate_recovery_diffs(self, diffs: list[tuple[str, str]]) -> str:
        """在临时 worktree 顺序重放 diff，返回合并后的 unified diff。"""

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
            for task_id, diff_text in diffs:
                ok, detail = apply_unified_diff_to_workspace(
                    environment.active_workspace,
                    diff_text,
                    check_only=False,
                )
                if not ok:
                    raise RuntimeError(
                        f"fanout resume diff failed for {task_id}: {detail}"
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


def worker_task_prompt(
    goal: str,
    task: SubagentTask,
    dependency_handoffs: list[WorkerHandoff] | None = None,
) -> str:
    """构造隔离 worker 的最小任务上下文。"""

    handoffs = dependency_handoffs or []
    return "\n".join(
        [
            "You are an isolated worker in a coordinator-driven fanout run.",
            f"task_id={task.id}",
            f"Fanout goal: {goal}",
            f"Worker task: {task.task}",
            f"Declared write scope: {task.write_scope or 'read-only'}",
            f"Expected artifact: {task.expected_artifact}",
            f"Acceptance criteria: {task.acceptance_criteria or 'none specified'}",
            "Direct dependency handoffs (no private conversations or full traces):",
            json.dumps(
                [handoff.to_dict() for handoff in handoffs],
                ensure_ascii=False,
                sort_keys=True,
            )[:6000]
            if handoffs
            else "[]",
            "Implement only this task. Do not touch paths outside the declared scope.",
            "Return a concise evidence-grounded result after using the available tools.",
        ]
    )


def finalizer_task_prompt(
    goal: str,
    results: list[LiveSubagentResult],
    *,
    plan: FanoutPlan | None = None,
) -> str:
    """构造只读 finalizer 的证据输入。"""

    rows = [
        (
            f"- {result.task_id}: {result.status}; touched={result.touched_files}; "
            f"artifact={sanitize_quoted_evidence(result.artifact_path)}; "
            f"handoff={sanitize_quoted_evidence(json.dumps(result.handoff.to_dict(), ensure_ascii=False)[:1600] if result.handoff else result.final_answer[:1200])}"
        )
        for result in results
    ]
    criteria = _all_criteria(plan) if plan is not None else []
    criterion_rows = [
        f"{index}. {criterion}" for index, criterion in enumerate(criteria, start=1)
    ]
    return "\n".join(
        [
            "You are FanoutFinalizer, the final read-only integration verifier.",
            f"Goal: {goal}",
            "Use worker outputs as primary evidence. Inspect git_status/git_diff once when needed.",
            "Run python_validation only when integrated code changes need a focused check.",
            "Do not explore unrelated files. Use at most two tool-call rounds.",
            "For every criterion, output CRITERION <number>: PASS|FAIL|UNKNOWN | evidence.",
            "Then output FINAL: PASS, FINAL: NEEDS_REVISION, or FINAL: BLOCKED.",
            "Do not modify files.",
            "Required acceptance criteria:",
            *(criterion_rows or ["(none specified)"]),
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
    validation_evidence: list[dict[str, object]],
) -> str:
    return "\n".join(
        [
            f"# Subagent {task.id}",
            "",
            f"- status: `{status}`",
            f"- write_scope: `{task.write_scope}`",
            f"- touched_files: `{touched_files}`",
            f"- error: `{error}`",
            f"- acceptance_criteria: `{task.acceptance_criteria}`",
            f"- validation_evidence: `{validation_evidence}`",
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


def _trace_outcome(
    trace_path: Path,
) -> tuple[str, str, bool, list[dict[str, object]]]:
    """只从 canonical worker trace 读取 retryability 和 validation 事实。"""

    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    stop_reason = str(payload.get("stop_reason") or "")
    failure_kind = ""
    retryable = False
    validation_evidence: list[dict[str, object]] = []
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        if event.get("event_type") == "recovery_decision":
            failure_kind = str(event.get("failure_kind") or "")
            retryable = event.get("retryable") is True
        if event.get("event_type") == "validation_evidence" and isinstance(
            event.get("validation"), dict
        ):
            validation_evidence.append(dict(event["validation"]))
    return stop_reason, failure_kind, retryable, validation_evidence


def _unresolved_issues(status: str, error: str) -> list[str]:
    if status == "completed":
        return []
    return [error or f"worker ended with status {status}"]


def _all_criteria(plan: FanoutPlan | None) -> list[str]:
    if plan is None:
        return []
    criteria = list(plan.global_acceptance_criteria)
    for task in plan.tasks:
        criteria.extend(task.acceptance_criteria)
    return list(dict.fromkeys(criteria))


def _criterion_results(answer: str, criteria: list[str]) -> list[CriterionResult]:
    observed: dict[int, tuple[str, str]] = {}
    pattern = re.compile(
        r"^CRITERION\s+(\d+)\s*:\s*(PASS|FAIL|UNKNOWN)(?:\s*\|\s*(.*))?$",
        re.IGNORECASE,
    )
    for line in (answer or "").splitlines()[:160]:
        match = pattern.match(line.strip().strip("*`"))
        if not match:
            continue
        observed[int(match.group(1))] = (
            match.group(2).upper(),
            (match.group(3) or "").strip()[:1000],
        )
    return [
        CriterionResult(
            criterion=criterion,
            status=observed.get(index, ("UNKNOWN", "missing criterion result"))[0],
            evidence=observed.get(index, ("UNKNOWN", "missing criterion result"))[1],
        )
        for index, criterion in enumerate(criteria, start=1)
    ]


def _has_failed_runtime_evidence(
    plan: FanoutPlan,
    results: list[LiveSubagentResult],
) -> bool:
    task_by_id = {task.id: task for task in plan.tasks}
    for result in results:
        if result.status != "completed":
            return True
        task = task_by_id.get(result.task_id)
        if task is None:
            return True
        if task.write_scope:
            candidate = Path(result.candidate_diff_path)
            if (
                not candidate.is_file()
                or not candidate.read_text(encoding="utf-8").strip()
            ):
                return True
        if any(
            str(item.get("status") or "").lower() not in {"", "passed"}
            for item in result.validation_evidence
        ):
            return True
    return False


def _decision(answer: str) -> str:
    decisions: set[str] = set()
    for line in (answer or "").splitlines()[:80]:
        normalized = line.strip().strip("*#:- `").upper()
        for marker in ("PASS", "NEEDS_REVISION", "BLOCKED"):
            if normalized in {
                marker,
                f"VERDICT: {marker}",
                f"STATUS: {marker}",
                f"DECISION: {marker}",
                f"FINAL: {marker}",
            }:
                decisions.add(marker)
    if len(decisions) == 1:
        return decisions.pop()
    return "NEEDS_REVISION"


_finalizer_task = finalizer_task_prompt
