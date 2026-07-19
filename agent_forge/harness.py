"""NanoHarness 的稳定嵌入式 Public API。

业务调用方只需要阅读 ``Harness.run``。AgentLoop、wiring 和各 application service
继续属于内部实现；高级使用者通过 ``HarnessExtensions`` 替换已有 Port。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from agent_forge.hooks import RuntimeHook
from agent_forge.context.ports import LongTermMemoryRecallPort
from agent_forge.observability.adapters.streaming import (
    EventStreamPolicy,
    StreamingEventSink,
)
from agent_forge.observability.api import TraceRecorder, write_usage_artifacts
from agent_forge.observability.ports import RuntimeEventListener
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.task import (
    TaskCheckpoint,
    TaskCheckpointUpdate,
    TaskRunStatus,
    TaskStartRequest,
)
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.execution_environment import (
    ExecutionEnvironment,
    ExecutionEnvironmentConfig,
)
from agent_forge.runtime.ports import (
    ApprovalRepository,
    ContextAssemblerPort,
    EnvironmentPort,
    EventSink,
    HookPort,
    HumanInputRepository,
    ModelPort,
    OperationLedgerRepository,
    RunControlPort,
    SkillSelectorPort,
    TaskStateRepository,
    ToolGateway,
)
from agent_forge.runtime.wiring import (
    AgentLoopBuildRequest,
    RuntimeDependencyOverrides,
    ToolRegistryBuildRequest,
    build_agent_loop_from_request,
    build_registry,
    build_task_state_repository,
    load_task_checkpoint,
)


@dataclass(frozen=True)
class HarnessConfig:
    """可跨多次运行复用的 Runtime 策略，不包含模型密钥。"""

    workspace: str = "."
    output_root: str = ".agent_forge/runs"
    max_steps: int = 12
    max_context_chars: int = 8_000
    max_prompt_tokens: int = 32_768
    reserved_output_tokens: int = 4_096
    max_consecutive_failures: int = 3
    max_tool_repeats: int = 2
    max_tool_calls_per_turn: int = 4
    timeout_seconds: float = 120.0
    cost_budget_usd: float | None = None
    approval_mode: str = "trusted"
    auto_approve_writes: bool = True
    skill_mode: str = "auto"
    skill_names: tuple[str, ...] = ()
    skill_manifest_files: tuple[str, ...] = ()
    tool_routing_mode: str = "task-aware"
    enabled_tools: tuple[str, ...] | None = None
    memory_recall_limit: int = 6
    model_capabilities: ModelCapabilities | None = None
    instruction_target: str = ""
    global_instruction_files: tuple[str, ...] = ()
    runtime_instructions: str = ""
    instruction_max_bytes: int = 2_600

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be positive")
        if self.max_context_chars < 1 or self.max_prompt_tokens < 1:
            raise ValueError("context budgets must be positive")
        if not 0 <= self.reserved_output_tokens < self.max_prompt_tokens:
            raise ValueError("reserved_output_tokens must be below max_prompt_tokens")
        if self.approval_mode not in {
            "trusted",
            "on-write",
            "on-risk",
            "locked",
            "dry-run",
        }:
            raise ValueError(f"unsupported approval_mode: {self.approval_mode}")
        if self.tool_routing_mode not in {"task-aware", "all"}:
            raise ValueError(f"unsupported tool_routing_mode: {self.tool_routing_mode}")
        if self.skill_mode not in {"auto", "none"}:
            raise ValueError(f"unsupported skill_mode: {self.skill_mode}")
        if self.max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be positive")
        if self.max_tool_repeats < 0:
            raise ValueError("max_tool_repeats must not be negative")
        if self.max_tool_calls_per_turn < 1:
            raise ValueError("max_tool_calls_per_turn must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.cost_budget_usd is not None and self.cost_budget_usd < 0:
            raise ValueError("cost_budget_usd must not be negative")
        if self.memory_recall_limit < 0:
            raise ValueError("memory_recall_limit must not be negative")
        if self.instruction_max_bytes < 1:
            raise ValueError("instruction_max_bytes must be positive")


@dataclass(frozen=True)
class RunRequest:
    """一次 Public API 运行的最小输入；空字段继承 ``HarnessConfig``。"""

    task: str
    workspace: str = ""
    output_root: str = ""
    agent_name: str = "CodingAgent"
    resume_state: str = ""
    human_thread_id: str = ""

    def validate(self) -> None:
        if not self.task.strip():
            raise ValueError("task must not be empty")
        if not self.agent_name.strip():
            raise ValueError("agent_name must not be empty")


@dataclass(frozen=True)
class RunResult:
    """一次运行的类型化结论和可追溯 artifact 入口。"""

    run_id: str
    status: TaskRunStatus
    stop_reason: str
    final_answer: str
    artifact_dir: Path
    checkpoint: TaskCheckpoint
    trace_path: Path | None = None
    usage_path: Path | None = None
    patch_path: Path | None = None

    @property
    def waiting_for_operator(self) -> bool:
        """返回运行是否停在人工回答或审批边界。"""

        return self.status in {
            TaskRunStatus.WAITING_APPROVAL,
            TaskRunStatus.WAITING_HUMAN,
            TaskRunStatus.PAUSED,
        }


class EventSinkFactory(Protocol):
    """为每次 run 创建独立 EventSink，避免复用 run identity。"""

    def create(self, trace_path: str) -> EventSink:
        """返回尚未发布、具有独立 ``run_id`` 的 sink。"""


@dataclass(frozen=True)
class HarnessExtensions:
    """高级调用方可以替换的稳定 Port 集合；未提供项使用内置 Adapter。"""

    context_assembler: ContextAssemblerPort | None = None
    checkpoint_repository: TaskStateRepository | None = None
    event_sink_factory: EventSinkFactory | None = None
    event_listeners: tuple[RuntimeEventListener, ...] = ()
    event_stream_policy: EventStreamPolicy = field(default_factory=EventStreamPolicy)
    execution_environment: EnvironmentPort | None = None
    skill_selector: SkillSelectorPort | None = None
    approval_repository: ApprovalRepository | None = None
    human_input_repository: HumanInputRepository | None = None
    operation_repository: OperationLedgerRepository | None = None
    long_term_memory_recall: LongTermMemoryRecallPort | None = None
    run_control: RunControlPort | None = None
    lifecycle_hooks: tuple[RuntimeHook, ...] = ()
    hook_policy: HookPort | None = None


class Harness:
    """面向嵌入调用方的单 Agent Harness facade。

    ``model`` 是唯一必需依赖；不传 ``tools`` 时使用当前 coding-tool preset。
    调用方无需了解 RunPreparation、ToolExecutionPipeline 或 RunLifecycle。
    """

    def __init__(
        self,
        *,
        model: ModelPort,
        tools: ToolGateway | None = None,
        config: HarnessConfig | None = None,
        extensions: HarnessExtensions | None = None,
    ) -> None:
        self._model = model
        self._tools = tools
        self._config = config or HarnessConfig()
        self._extensions = extensions or HarnessExtensions()
        if tools is not None and self._config.enabled_tools is not None:
            raise ValueError(
                "enabled_tools config only applies to the built-in coding-tool preset"
            )
        if self._extensions.hook_policy is not None and self._extensions.lifecycle_hooks:
            raise ValueError(
                "lifecycle_hooks cannot be combined with a full hook_policy override"
            )
        if self._extensions.hook_policy is not None and (
            self._extensions.execution_environment is None or tools is None
        ):
            raise ValueError(
                "a full hook_policy override requires a custom execution_environment "
                "and custom tools; use lifecycle_hooks to extend the default safety chain"
            )

    # 主要入口：创建 artifact、装配端口并执行规范 AgentLoop。
    def run(self, request: str | RunRequest) -> RunResult:
        """执行任务并返回状态、checkpoint 和 evidence 路径。"""

        normalized = request if isinstance(request, RunRequest) else RunRequest(request)
        normalized.validate()
        workspace = Path(normalized.workspace or self._config.workspace).resolve()
        output_root = Path(normalized.output_root or self._config.output_root)
        run_dir = output_root / _new_run_directory_name()
        run_dir.mkdir(parents=True, exist_ok=False)

        trace_path = run_dir / "trace.json"
        default_trace = self._extensions.event_sink_factory is None
        base_events = (
            self._extensions.event_sink_factory.create(str(trace_path))
            if self._extensions.event_sink_factory is not None
            else TraceRecorder(str(trace_path))
        )
        events: EventSink = (
            StreamingEventSink(
                base_events,
                self._extensions.event_listeners,
                self._extensions.event_stream_policy,
            )
            if self._extensions.event_listeners
            else base_events
        )
        events.set_run_context(task=normalized.task)
        concrete_environment: ExecutionEnvironment | None = None
        environment_prepared = False
        try:
            environment: EnvironmentPort
            if self._extensions.execution_environment is None:
                concrete_environment = ExecutionEnvironment(
                    ExecutionEnvironmentConfig(
                        mode="local",
                        workspace=str(workspace),
                        run_id=run_dir.name,
                        network_policy="deny",
                    )
                )
                concrete_environment.prepare()
                environment_prepared = True
                environment = concrete_environment
            else:
                custom_environment = self._extensions.execution_environment
                if self._extensions.hook_policy is None or self._tools is None:
                    raise ValueError(
                        "a custom execution environment requires matching "
                        "hook_policy and tools"
                    )
                environment = custom_environment

            return self._execute_run(
                normalized,
                run_dir,
                events,
                environment,
                concrete_environment,
            )
        finally:
            try:
                events.publish()
                if default_trace:
                    write_usage_artifacts(trace_path)
            finally:
                if concrete_environment is not None:
                    try:
                        if environment_prepared:
                            concrete_environment.write_manifest(run_dir)
                    finally:
                        concrete_environment.cleanup()

    def _execute_run(
        self,
        request: RunRequest,
        run_dir: Path,
        events: EventSink,
        environment: EnvironmentPort,
        concrete_environment: ExecutionEnvironment | None,
    ) -> RunResult:
        """在已准备环境中装配唯一 AgentLoop，并构造 Public API 结果。"""

        workspace = Path(request.workspace or self._config.workspace).resolve()
        trace_path = run_dir / "trace.json"
        environment_evidence = environment.probe().to_dict()
        active_workspace_value = environment_evidence.get("active_workspace")
        active_workspace = (
            Path(active_workspace_value).resolve()
            if isinstance(active_workspace_value, str)
            else workspace
        )
        gateway = self._tools or build_registry(
            ToolRegistryBuildRequest(
                workspace=str(active_workspace),
                auto=self._config.auto_approve_writes,
                enabled_tools=self._config.enabled_tools,
                execution_environment=concrete_environment,
            )
        )
        checkpoint_repository = _TrackingTaskStateRepository(
            self._extensions.checkpoint_repository
            or build_task_state_repository(run_dir / "task_state")
        )
        runtime_config = self._runtime_config(
            request,
            workspace=active_workspace,
            run_dir=run_dir,
            trace_path=trace_path,
            environment=environment,
        )
        overrides = RuntimeDependencyOverrides(
            context=self._extensions.context_assembler,
            skills=self._extensions.skill_selector,
            environment=environment,
            hooks=self._extensions.hook_policy,
            additional_hooks=self._extensions.lifecycle_hooks,
            task_states=checkpoint_repository,
            approvals=self._extensions.approval_repository,
            human_inputs=self._extensions.human_input_repository,
            operations=self._extensions.operation_repository,
            long_term_memory_recall=self._extensions.long_term_memory_recall,
            control=self._extensions.run_control,
        )
        events.add(
            0,
            "Runtime",
            "execution_environment",
            execution_environment=environment_evidence,
        )
        _write_request_artifact(run_dir, request, self._config)

        final_answer = build_agent_loop_from_request(
            AgentLoopBuildRequest(
                config=runtime_config,
                trace=events,
                registry=gateway,
                llm=self._model,
                overrides=overrides,
            )
        ).run(request.task, agent_name=request.agent_name)
        (run_dir / "final_answer.txt").write_text(final_answer, encoding="utf-8")
        if concrete_environment is not None:
            (run_dir / "patch.diff").write_text(
                concrete_environment.diff(),
                encoding="utf-8",
            )

        checkpoint = checkpoint_repository.latest
        if checkpoint is None:
            raise RuntimeError("AgentLoop completed without creating a checkpoint")
        status = TaskRunStatus(checkpoint.status)
        default_trace = self._extensions.event_sink_factory is None
        return RunResult(
            run_id=events.run_id,
            status=status,
            stop_reason=checkpoint.stop_reason,
            final_answer=final_answer,
            artifact_dir=run_dir,
            checkpoint=checkpoint,
            trace_path=trace_path if default_trace else None,
            usage_path=(run_dir / "usage.json") if default_trace else None,
            patch_path=(run_dir / "patch.diff") if concrete_environment else None,
        )

    # 主要入口：从 durable checkpoint 创建一次显式 continuation。
    def resume(
        self,
        checkpoint_path: str | Path,
        *,
        task: str = "",
    ) -> RunResult:
        """加载 checkpoint，并用新的 run 继续，不声称恢复隐藏模型状态。"""

        path = str(checkpoint_path)
        repository = self._extensions.checkpoint_repository
        checkpoint = (
            repository.load_path(path)
            if repository is not None
            else load_task_checkpoint(path)
        )
        return self.run(
            RunRequest(
                task=task or checkpoint.task,
                workspace=checkpoint.workspace,
                agent_name=checkpoint.agent_name,
                resume_state=path,
                human_thread_id=str(checkpoint.metadata.get("human_thread_id") or ""),
            )
        )

    def _runtime_config(
        self,
        request: RunRequest,
        *,
        workspace: Path,
        run_dir: Path,
        trace_path: Path,
        environment: EnvironmentPort,
    ) -> RuntimeConfig:
        control_root = workspace / ".agent_forge"
        return RuntimeConfig(
            workspace=str(workspace),
            max_steps=self._config.max_steps,
            auto_approve_writes=self._config.auto_approve_writes,
            trace_file=str(trace_path),
            max_context_chars=self._config.max_context_chars,
            max_prompt_tokens=self._config.max_prompt_tokens,
            reserved_output_tokens=self._config.reserved_output_tokens,
            max_consecutive_failures=self._config.max_consecutive_failures,
            max_tool_repeats=self._config.max_tool_repeats,
            max_tool_calls_per_turn=self._config.max_tool_calls_per_turn,
            timeout_seconds=self._config.timeout_seconds,
            cost_budget_usd=self._config.cost_budget_usd,
            execution_environment=environment,
            task_state_root=str(run_dir / "task_state"),
            resume_state=request.resume_state,
            approval_root=str(control_root / "approvals"),
            human_input_root=str(control_root / "human_input"),
            human_thread_id=request.human_thread_id,
            operation_ledger_root=str(control_root / "operation_ledger"),
            approval_mode=self._config.approval_mode,
            skill_mode=self._config.skill_mode,
            skill_names=list(self._config.skill_names),
            skill_manifest_files=list(self._config.skill_manifest_files),
            tool_routing_mode=self._config.tool_routing_mode,
            memory_root=str(control_root / "memory"),
            memory_namespace=str(workspace),
            memory_recall_limit=self._config.memory_recall_limit,
            model_capabilities=self._config.model_capabilities,
            instruction_target=self._config.instruction_target,
            global_instruction_files=list(self._config.global_instruction_files),
            runtime_instructions=self._config.runtime_instructions,
            instruction_max_bytes=self._config.instruction_max_bytes,
        )


class _TrackingTaskStateRepository:
    """捕获 Port 的最新值，让 facade 无需读取具体 JSON Adapter。"""

    def __init__(self, delegate: TaskStateRepository) -> None:
        self._delegate = delegate
        self.latest: TaskCheckpoint | None = None

    def start(self, request: TaskStartRequest) -> TaskCheckpoint:
        self.latest = self._delegate.start(request)
        return self.latest

    def update(
        self,
        checkpoint: TaskCheckpoint,
        update: TaskCheckpointUpdate,
    ) -> TaskCheckpoint:
        self.latest = self._delegate.update(checkpoint, update)
        return self.latest

    def load_path(self, path: str) -> TaskCheckpoint:
        return self._delegate.load_path(path)


def _new_run_directory_name() -> str:
    return f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:7]}"


def _write_request_artifact(
    run_dir: Path,
    request: RunRequest,
    config: HarnessConfig,
) -> None:
    config_payload = asdict(config)
    runtime_instructions = str(config_payload.pop("runtime_instructions", "") or "")
    config_payload["runtime_instructions_configured"] = bool(runtime_instructions)
    config_payload["runtime_instructions_sha256"] = (
        hashlib.sha256(runtime_instructions.encode("utf-8")).hexdigest()
        if runtime_instructions
        else ""
    )
    payload = {
        "schema_version": 1,
        "request": asdict(request),
        "config": config_payload,
    }
    (run_dir / "run_request.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


__all__ = [
    "Harness",
    "HarnessConfig",
    "HarnessExtensions",
    "EventSinkFactory",
    "RunRequest",
    "RunResult",
]
