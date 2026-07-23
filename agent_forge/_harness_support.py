"""Harness 的准备与落盘细节。

本文件不是主阅读路径。它集中收纳路径构造、请求快照、事件出口和 checkpoint
跟踪等机械工作，让 ``Harness`` 只呈现运行时装配与执行顺序。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from agent_forge.harness_contracts import (
    HarnessConfig,
    HarnessExtensions,
    RunRequest,
    RunResult,
)
from agent_forge.observability.adapters.streaming import StreamingEventSink
from agent_forge.observability.api import (
    TraceRecorder,
    write_run_manifest,
    write_usage_artifacts,
)
from agent_forge.runtime.execution_environment import ExecutionEnvironment
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.task import (
    TaskCheckpoint,
    TaskCheckpointUpdate,
    TaskStartRequest,
)
from agent_forge.runtime.ports import EnvironmentPort, EventSink, TaskStateRepository


@dataclass(frozen=True)
class HarnessRunPaths:
    """本次运行会用到的全部路径；只构造名称，不执行 Runtime。"""

    requested_workspace: Path
    artifact_dir: Path
    trace_file: Path
    final_answer_file: Path
    patch_file: Path
    task_state_dir: Path
    manifest_file: Path


class TrackingTaskStateRepository:
    """代理 checkpoint Port，并保留最近状态供 ``RunResult`` 收口。"""

    def __init__(self, delegate: TaskStateRepository) -> None:
        self._delegate = delegate
        self.latest: TaskCheckpoint | None = None

    def start(self, request: TaskStartRequest) -> TaskCheckpoint:
        """创建首个 checkpoint，并记录为本次运行的最新状态。"""

        self.latest = self._delegate.start(request)
        return self.latest

    def update(
        self,
        checkpoint: TaskCheckpoint,
        update: TaskCheckpointUpdate,
    ) -> TaskCheckpoint:
        """持久化状态变化，并刷新最新 checkpoint 引用。"""

        self.latest = self._delegate.update(checkpoint, update)
        return self.latest

    def load_path(self, path: str) -> TaskCheckpoint:
        """把恢复读取原样委托给调用方选择的 Repository。"""

        return self._delegate.load_path(path)


def create_run_paths(
    request: RunRequest,
    config: HarnessConfig,
) -> HarnessRunPaths:
    """集中构造 run 目录及固定 artifact 文件名。"""

    requested_workspace = Path(request.workspace or config.workspace).resolve()
    output_root = Path(request.output_root or config.output_root)
    artifact_dir = output_root / _new_run_directory_name()
    return HarnessRunPaths(
        requested_workspace=requested_workspace,
        artifact_dir=artifact_dir,
        trace_file=artifact_dir / "trace.json",
        final_answer_file=artifact_dir / "final_answer.txt",
        patch_file=artifact_dir / "patch.diff",
        task_state_dir=artifact_dir / "task_state",
        manifest_file=artifact_dir / "run_manifest.json",
    )


def create_event_sink(
    extensions: HarnessExtensions,
    trace_file: Path,
) -> tuple[EventSink, bool]:
    """创建唯一事件出口，并返回是否使用内置 JSON trace。"""

    uses_default_trace = extensions.event_sink_factory is None
    base_event_sink = (
        extensions.event_sink_factory.create(str(trace_file))
        if extensions.event_sink_factory is not None
        else TraceRecorder(str(trace_file))
    )
    event_sink: EventSink = (
        StreamingEventSink(
            base_event_sink,
            extensions.event_listeners,
            extensions.event_stream_policy,
        )
        if extensions.event_listeners
        else base_event_sink
    )
    return event_sink, uses_default_trace


def build_runtime_config(
    config: HarnessConfig,
    request: RunRequest,
    *,
    workspace: Path,
    run_dir: Path,
    trace_path: Path,
    environment: EnvironmentPort,
) -> RuntimeConfig:
    """把 Public API 配置机械映射成内部 RuntimeConfig。"""

    requested_workspace = Path(request.workspace or config.workspace).resolve()
    return RuntimeConfig(
        workspace=str(workspace),
        max_steps=config.max_steps,
        auto_approve_writes=config.auto_approve_writes,
        trace_file=str(trace_path),
        max_context_chars=config.max_context_chars,
        max_prompt_tokens=config.max_prompt_tokens,
        reserved_output_tokens=config.reserved_output_tokens,
        max_consecutive_failures=config.max_consecutive_failures,
        max_tool_repeats=config.max_tool_repeats,
        max_tool_calls_per_turn=config.max_tool_calls_per_turn,
        timeout_seconds=config.timeout_seconds,
        cost_budget_usd=config.cost_budget_usd,
        execution_environment=environment,
        task_state_root=str(run_dir / "task_state"),
        resume_state=request.resume_state,
        approval_root=str(
            control_path(config.approval_root, requested_workspace, "approvals")
        ),
        human_input_root=str(
            control_path(config.human_input_root, requested_workspace, "human_input")
        ),
        human_thread_id=request.human_thread_id,
        operation_ledger_root=str(
            control_path(
                config.operation_ledger_root,
                requested_workspace,
                "operation_ledger",
            )
        ),
        approval_mode=config.approval_mode,
        skill_mode=config.skill_mode,
        skill_names=list(config.skill_names),
        skill_manifest_files=list(config.skill_manifest_files),
        tool_routing_mode=config.tool_routing_mode,
        memory_root=str(
            control_path(config.memory_root, requested_workspace, "memory")
        ),
        memory_namespace=str(requested_workspace),
        memory_recall_limit=config.memory_recall_limit,
        model_capabilities=config.model_capabilities,
        instruction_target=config.instruction_target,
        global_instruction_files=list(config.global_instruction_files),
        runtime_instructions=config.runtime_instructions,
        instruction_max_bytes=config.instruction_max_bytes,
    )


def finalize_run_artifacts(
    *,
    request: RunRequest,
    paths: HarnessRunPaths,
    events: EventSink,
    uses_default_trace: bool,
    owned_environment: ExecutionEnvironment | None,
    owned_environment_is_prepared: bool,
    result: RunResult | None,
    failure_stop_reason: str,
) -> None:
    """发布事件、清理执行环境，并保存一次 run 的最终 manifest。"""

    try:
        events.publish()
        if uses_default_trace:
            write_usage_artifacts(paths.trace_file)
    finally:
        if owned_environment is not None:
            try:
                if owned_environment_is_prepared:
                    owned_environment.write_manifest(paths.artifact_dir)
            finally:
                owned_environment.cleanup()

    write_run_manifest(
        paths.artifact_dir,
        run_id=result.run_id if result is not None else events.run_id,
        task=request.task,
        status=result.status.value if result is not None else "failed",
        stop_reason=(result.stop_reason if result is not None else failure_stop_reason),
    )


def write_request_artifact(
    run_dir: Path,
    request: RunRequest,
    config: HarnessConfig,
) -> None:
    """保存脱敏后的调用输入，支撑复现而不泄漏指令正文。"""

    config_payload = asdict(config)
    runtime_instructions = str(config_payload.pop("runtime_instructions", "") or "")
    config_payload["runtime_instructions_configured"] = bool(runtime_instructions)
    config_payload["runtime_instructions_sha256"] = (
        hashlib.sha256(runtime_instructions.encode("utf-8")).hexdigest()
        if runtime_instructions
        else ""
    )
    request_payload = asdict(request)
    resolved_config = request_payload.pop("resolved_config", None)
    payload = {
        "schema_version": 1,
        "request": request_payload,
        "config": config_payload,
    }
    (run_dir / "run_request.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if resolved_config is not None:
        (run_dir / "resolved_config.json").write_text(
            json.dumps(resolved_config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def control_path(value: str, workspace: Path, default_name: str) -> Path:
    """把控制面目录稳定落在请求 workspace，而不是临时 worktree。"""

    if not value:
        return workspace / ".agent_forge" / default_name
    path = Path(value)
    return path if path.is_absolute() else workspace / path


def write_latest_run_pointer(workspace: Path, run_dir: Path) -> None:
    """发布最近一次成功创建的 run，供 inspection/Workbench 发现。"""

    latest = workspace / ".agent_forge" / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "run.txt").write_text(str(run_dir.resolve()), encoding="utf-8")


def _new_run_directory_name() -> str:
    """生成便于人工排序且避免并发冲突的 run 目录名。"""

    return f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:7]}"
