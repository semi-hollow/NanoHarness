"""基于本地 worktree 和真实 AgentLoop 的 fanout worker adapter。"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from agent_forge.contracts import WORKSPACE_WRITE_TOOL_NAMES
from agent_forge.observability.adapters.json_trace import TraceRecorder
from agent_forge.observability.api import write_usage_artifacts
from agent_forge.runtime.api import build_agent_loop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.execution_environment import (
    ExecutionEnvironment,
    ExecutionEnvironmentConfig,
)
from agent_forge.runtime.git_workspace import (
    collect_changed_files,
    collect_workspace_diff,
)
from agent_forge.runtime.ports import RunControlPort
from agent_forge.runtime.ports.model import ModelPort
from agent_forge.runtime.wiring import (
    AgentLoopBuildRequest,
    RuntimeDependencyOverrides,
    build_agent_loop_from_request,
)
from agent_forge.safety.guardrails import sanitize_quoted_evidence
from agent_forge.tools.registry import ToolRegistry
from agent_forge.tools.base import Tool

from ..domain.fanout import SubagentTask
from ..domain.live import (
    FanoutPlan,
    FinalizerResult,
    LiveSubagentResult,
)
from ..ports import FanoutWorkerPort
from .git_workspace import apply_unified_diff_to_workspace, commit_worker_baseline

READ_TOOLS = {
    "list_files",
    "read_file",
    "grep_search",
    "git_status",
    "git_diff",
    "python_validation",
    "ask_human",
}
COORDINATION_TOOLS = {"publish_handoff_event"}
FINALIZER_READ_TOOLS = {"git_status", "git_diff", "python_validation"}
WRITE_TOOLS = {*READ_TOOLS, *WORKSPACE_WRITE_TOOL_NAMES, "run_command"}

RegistryFactory = Callable[[Path, ExecutionEnvironment], ToolRegistry]
LLMFactory = Callable[[], ModelPort]


@dataclass(frozen=True, kw_only=True)
class AgentWorkerRuntimeOptions:
    """Live adapter 复用本地 Worker substrate 时允许替换的窄接口。"""

    control: RunControlPort | None = None
    extra_tools: tuple[Tool, ...] = ()
    model: ModelPort | None = None
    task_prompt: str = ""
    agent_name: str = ""


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
    ) -> LiveSubagentResult:
        """在临时 worktree 中运行一个受 scope 限制的 AgentLoop。"""

        return self.run_worker_with_options(
            task,
            batch_index,
            base_diff_text,
        )

    def run_worker_with_options(
        self,
        task: SubagentTask,
        batch_index: int,
        base_diff_text: str,
        *,
        options: AgentWorkerRuntimeOptions | None = None,
    ) -> LiveSubagentResult:
        """复用同一 worktree/diff substrate，并注入有界 Runtime 扩展。"""

        runtime_options = options or AgentWorkerRuntimeOptions()

        # region 1. 输出契约准备（实现细节）：固定目录，并先准备失败兜底值
        # 先创建所有稳定路径和默认失败值，保证后续任意阶段抛异常时仍能返回结构完整的
        # LiveSubagentResult，而不是让 Coordinator 依赖临时 worktree 内部状态。
        started = time.monotonic()
        worker_dir = self.root / "workers" / task.id
        worker_dir.mkdir(parents=True, exist_ok=True)
        trace_path = worker_dir / "trace.json"
        candidate_diff_path = worker_dir / "candidate_changes.diff"
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
        candidate_diff_sha256 = ""
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
            full_registry = self.registry_factory(active_workspace, environment)
            for extra_tool in runtime_options.extra_tools:
                full_registry.register(extra_tool)
            registry = _filtered_registry(full_registry, task)
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
                    f"fanout:{self.plan.digest[:16]}:{self.base_head[:12]}:{task.id}"
                ),
                operation_ledger_root=str(worker_dir / "operation_ledger"),
            )
            # endregion 3. 受限 Runtime 装配结束

            # region 4. AgentLoop 执行（主链）：任务契约进入模型，结果写入独立 Trace/Usage
            # worker_task_prompt 把总目标与子任务 scope 合并；AgentLoop 完成后立即冻结
            # Trace/Usage，后续候选收集只读取 workspace，不修改模型运行证据。
            worker_model = runtime_options.model or self.llm_factory()
            if runtime_options.control is None:
                worker_loop = build_agent_loop(
                    worker_config,
                    worker_trace,
                    registry,
                    worker_model,
                )
            else:
                worker_loop = build_agent_loop_from_request(
                    AgentLoopBuildRequest(
                        config=worker_config,
                        trace=worker_trace,
                        registry=registry,
                        llm=worker_model,
                        overrides=RuntimeDependencyOverrides(
                            control=runtime_options.control,
                        ),
                    )
                )
            final_answer = worker_loop.run(
                runtime_options.task_prompt or worker_task_prompt(self.plan.goal, task),
                agent_name=runtime_options.agent_name or f"Subagent:{task.id}",
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
            # endregion 5. 候选结果收集结束
        except Exception as exc:
            # 失败也必须发布结构稳定的 artifact；Coordinator 不需要靠异常猜 Worker 状态。
            error = str(exc)
            candidate_diff_path.write_text("", encoding="utf-8")
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
        )

    # 主要入口：在独立只读环境复核合并结果，且禁止 Finalizer 产生新改动。
    def run_finalizer(
        self,
        goal: str,
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
                finalizer_task_prompt(goal, results),
                agent_name="FanoutFinalizer",
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
                task=finalizer_task_prompt(goal, results),
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
    if not task.write_scope and allowed - READ_TOOLS - COORDINATION_TOOLS:
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
            "You are FanoutFinalizer, the final read-only integration verifier.",
            f"Goal: {goal}",
            "Use worker outputs as primary evidence. Inspect git_status/git_diff once when needed.",
            "Run python_validation only when integrated code changes need a focused check.",
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
    decisions: set[str] = set()
    for line in (answer or "").splitlines()[:80]:
        normalized = line.strip().strip("*#:- `").upper()
        for marker in ("PASS", "NEEDS_REVISION", "BLOCKED"):
            if normalized in {
                marker,
                f"VERDICT: {marker}",
                f"STATUS: {marker}",
                f"DECISION: {marker}",
            }:
                decisions.add(marker)
    if len(decisions) == 1:
        return decisions.pop()
    return "NEEDS_REVISION"


_finalizer_task = finalizer_task_prompt
