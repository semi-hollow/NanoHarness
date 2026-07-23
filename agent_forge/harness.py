"""NanoHarness 的稳定嵌入式 Public API。

业务调用方只需要阅读 ``Harness.run``。AgentLoop、wiring 和各 application service
继续属于内部实现；高级使用者通过 ``HarnessExtensions`` 替换已有 Port。
"""

from __future__ import annotations

from pathlib import Path

from agent_forge._harness_support import (
    HarnessRunPaths,
    TrackingTaskStateRepository,
    build_runtime_config,
    create_event_sink,
    create_run_paths,
    finalize_run_artifacts,
    write_latest_run_pointer,
    write_request_artifact,
)
from agent_forge.harness_contracts import (
    EventSinkFactory,
    HarnessConfig,
    HarnessExtensions,
    RunRequest,
    RunResult,
)
from agent_forge.runtime.domain.task import TaskRunStatus
from agent_forge.runtime.execution_environment import (
    ExecutionEnvironment,
    ExecutionEnvironmentConfig,
)
from agent_forge.runtime.ports import (
    EnvironmentPort,
    EventSink,
    ModelPort,
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
        """执行任务并返回状态、checkpoint 和 evidence 路径。

        流程位置：Single-Agent Public API 与六边形 composition root。
        规范上游：薄 CLI ``run`` 或嵌入式调用方。
        下一 owner：ExecutionEnvironment、Runtime wiring、``AgentLoop.run``。
        状态与证据：``RunResult``、checkpoint、trace、patch 与 RunManifest。
        系统不变量：外围不得复制 Runtime 编排，也不得用 candidate patch 宣称 solved。
        删除/内联影响：会失去唯一装配 owner，并重新产生 CLI/demo wiring 漂移。
        """

        # region 准备区（首遍可折叠）：输入、路径与唯一事件出口
        run_request = (
            request if isinstance(request, RunRequest) else RunRequest(request)
        )
        run_request.validate()
        run_paths = create_run_paths(run_request, self._config)
        run_paths.artifact_dir.mkdir(parents=True, exist_ok=False)
        events, uses_default_trace = create_event_sink(
            self._extensions,
            run_paths.trace_file,
        )
        events.set_run_context(task=run_request.task)

        # 执行状态（仅供 finally 收口）：owned 表示由 Harness 创建并负责清理。
        owned_environment: ExecutionEnvironment | None = None
        owned_environment_is_prepared = False
        run_result: RunResult | None = None
        failure_stop_reason = ""
        # endregion 准备区结束
        try:
            environment: EnvironmentPort
            if self._extensions.execution_environment is None:
                owned_environment = self._build_owned_environment(
                    run_paths.requested_workspace,
                    run_paths.artifact_dir.name,
                )
                owned_environment.prepare()
                owned_environment_is_prepared = True
                environment = owned_environment
            else:
                custom_environment = self._extensions.execution_environment
                if self._extensions.hook_policy is None or self._tools is None:
                    raise ValueError(
                        "a custom execution environment requires matching "
                        "hook_policy and tools"
                    )
                environment = custom_environment

            # 主执行区：环境准备完成后，唯一地进入 Runtime 装配与 AgentLoop。
            run_result = self._execute_run(
                run_request,
                run_paths,
                events,
                environment,
                owned_environment,
            )
        except Exception as exc:
            failure_stop_reason = f"exception:{type(exc).__name__}"
            raise
        finally:
            finalize_run_artifacts(
                request=run_request,
                paths=run_paths,
                events=events,
                uses_default_trace=uses_default_trace,
                owned_environment=owned_environment,
                owned_environment_is_prepared=owned_environment_is_prepared,
                result=run_result,
                failure_stop_reason=failure_stop_reason,
            )
        if run_result is None:
            raise RuntimeError("Harness run ended without a typed result")
        write_latest_run_pointer(
            run_paths.requested_workspace,
            run_paths.artifact_dir,
        )
        return run_result

    # region Runtime 装配细节（首次阅读可折叠）
    def _execute_run(
        self,
        request: RunRequest,
        run_paths: HarnessRunPaths,
        events: EventSink,
        environment: EnvironmentPort,
        owned_environment: ExecutionEnvironment | None,
    ) -> RunResult:
        """在已准备环境中装配唯一 AgentLoop，并构造 Public API 结果。"""

        # region 准备区（首遍可折叠）：环境探测结果与 Runtime 依赖
        environment_evidence = environment.probe().to_dict()
        active_workspace_from_probe = environment_evidence.get(
            "active_workspace"
        )
        runtime_workspace = (
            Path(active_workspace_from_probe).resolve()
            if isinstance(active_workspace_from_probe, str)
            else run_paths.requested_workspace
        )
        tool_gateway = self._tools or build_registry(
            ToolRegistryBuildRequest(
                workspace=str(runtime_workspace),
                auto=self._config.auto_approve_writes,
                enabled_tools=self._config.enabled_tools,
                mcp_config_file=self._config.mcp_config_file,
                mcp_allowed_tools=self._config.mcp_allowed_tools,
                execution_environment=owned_environment,
            )
        )
        tracked_task_states = TrackingTaskStateRepository(
            self._extensions.checkpoint_repository
            or build_task_state_repository(run_paths.task_state_dir)
        )
        runtime_config = build_runtime_config(
            self._config,
            request,
            workspace=runtime_workspace,
            run_dir=run_paths.artifact_dir,
            trace_path=run_paths.trace_file,
            environment=environment,
        )
        dependency_overrides = RuntimeDependencyOverrides(
            context=self._extensions.context_assembler,
            skills=self._extensions.skill_selector,
            environment=environment,
            hooks=self._extensions.hook_policy,
            additional_hooks=self._extensions.lifecycle_hooks,
            task_states=tracked_task_states,
            approvals=self._extensions.approval_repository,
            human_inputs=self._extensions.human_input_repository,
            operations=self._extensions.operation_repository,
            long_term_memory_recall=self._extensions.long_term_memory_recall,
            control=self._extensions.run_control,
        )
        # endregion 装配准备结束

        # 主执行区：记录环境事实，随后只调用一次规范 AgentLoop。
        events.add(
            0,
            "Runtime",
            "execution_environment",
            execution_environment=environment_evidence,
        )
        write_request_artifact(run_paths.artifact_dir, request, self._config)

        final_answer = build_agent_loop_from_request(
            AgentLoopBuildRequest(
                config=runtime_config,
                trace=events,
                registry=tool_gateway,
                llm=self._model,
                overrides=dependency_overrides,
            )
        ).run(request.task, agent_name=request.agent_name)
        run_paths.final_answer_file.write_text(final_answer, encoding="utf-8")
        if owned_environment is not None:
            run_paths.patch_file.write_text(
                owned_environment.diff(),
                encoding="utf-8",
            )

        # 收口区：只从最新 durable checkpoint 构造对外 RunResult。
        final_checkpoint = tracked_task_states.latest
        if final_checkpoint is None:
            raise RuntimeError("AgentLoop completed without creating a checkpoint")
        final_status = TaskRunStatus(final_checkpoint.status)
        uses_default_trace = self._extensions.event_sink_factory is None
        return RunResult(
            run_id=events.run_id,
            status=final_status,
            stop_reason=final_checkpoint.stop_reason,
            final_answer=final_answer,
            artifact_dir=run_paths.artifact_dir,
            checkpoint=final_checkpoint,
            trace_path=run_paths.trace_file if uses_default_trace else None,
            usage_path=(
                run_paths.artifact_dir / "usage.json"
                if uses_default_trace
                else None
            ),
            patch_path=(
                run_paths.patch_file
                if owned_environment is not None
                else None
            ),
            manifest_path=run_paths.manifest_file,
        )

    def _build_owned_environment(
        self,
        requested_workspace: Path,
        run_id: str,
    ) -> ExecutionEnvironment:
        """构造由 Harness 负责 prepare/manifest/cleanup 的执行环境。"""

        return ExecutionEnvironment(
            ExecutionEnvironmentConfig(
                mode=self._config.execution_mode,
                workspace=str(requested_workspace),
                run_id=run_id,
                network_policy=self._config.network_policy,
                keep_worktree=self._config.keep_worktree,
                container_runtime=self._config.container_runtime,
                container_image=self._config.container_image,
                container_cpus=self._config.container_cpus,
                container_memory=self._config.container_memory,
                container_pids_limit=self._config.container_pids_limit,
                container_read_only=self._config.container_read_only,
            )
        )
    # endregion Runtime 装配细节结束

    # 主要入口：从 durable checkpoint 创建一次显式 continuation。
    def resume(
        self,
        checkpoint_path: str | Path,
        *,
        task: str = "",
    ) -> RunResult:
        """加载 checkpoint，并用新的 run 继续，不声称恢复隐藏模型状态。"""

        resume_checkpoint_path = str(checkpoint_path)
        checkpoint_repository = self._extensions.checkpoint_repository
        restored_checkpoint = (
            checkpoint_repository.load_path(resume_checkpoint_path)
            if checkpoint_repository is not None
            else load_task_checkpoint(resume_checkpoint_path)
        )
        return self.run(
            RunRequest(
                task=task or restored_checkpoint.task,
                workspace=restored_checkpoint.workspace,
                agent_name=restored_checkpoint.agent_name,
                resume_state=resume_checkpoint_path,
                human_thread_id=str(
                    restored_checkpoint.metadata.get("human_thread_id") or ""
                ),
            )
        )


__all__ = [
    "Harness",
    "HarnessConfig",
    "HarnessExtensions",
    "EventSinkFactory",
    "RunRequest",
    "RunResult",
]
