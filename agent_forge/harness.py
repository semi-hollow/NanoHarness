"""NanoHarness 的稳定嵌入式 Public API。

业务调用方只需要阅读 ``Harness.run``。AgentLoop、wiring 和各 application service
继续属于内部实现；高级使用者通过 ``HarnessExtensions`` 替换已有 Port。
"""

from __future__ import annotations

import re
import shutil
import time
import uuid
from dataclasses import replace
from pathlib import Path

from agent_forge.contracts import JsonObject
from agent_forge._harness_support import (
    HarnessRunPaths,
    TrackingTaskStateRepository,
    build_runtime_config,
    create_event_sink,
    create_run_paths,
    control_path,
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
from agent_forge.runtime.domain.task import (
    RESUMABLE_RUN_STATUSES,
    TaskCheckpointUpdate,
    TaskRunStatus,
    TaskStartRequest,
)
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.thread import (
    ConversationItemDraft,
    ConversationThread,
    ThreadRun,
    Turn,
    TurnContextSnapshot,
)
from agent_forge.runtime.application.agent_loop import AgentLoop
from agent_forge.runtime.adapters.execution_environment import (
    ExecutionEnvironment,
    ExecutionEnvironmentConfig,
)
from agent_forge.runtime.ports import (
    EnvironmentPort,
    EventSink,
    ConversationThreadRepository,
    ModelPort,
    ToolGateway,
)
from agent_forge.runtime.wiring import (
    AgentLoopBuildRequest,
    RuntimeDependencyOverrides,
    ToolRegistryBuildRequest,
    build_agent_loop_from_request,
    build_registry,
    build_conversation_thread_repository,
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
        if (
            self._extensions.hook_policy is not None
            and self._extensions.lifecycle_hooks
        ):
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
        系统不变量：外围不得复制 Runtime 编排，也不得用 candidate diff 宣称 solved。
        删除/内联影响：会失去唯一装配 owner，并重新产生 CLI/demo wiring 漂移。
        """

        # region 准备区（实现细节）：输入、路径与唯一事件出口
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
        conversation_threads = self._conversation_repository(
            run_paths.storage_workspace
        )
        started_at = time.time()
        owned_environment: ExecutionEnvironment | None = None
        owned_environment_is_prepared = False
        tracked_task_states: TrackingTaskStateRepository | None = None
        environment: EnvironmentPort
        environment_evidence: JsonObject
        runtime_config = None
        agent_loop: AgentLoop | None = None
        runtime_assembly_started = False
        checkpoint_path = ""
        try:
            # region 1. 身份规划：只解析 Thread / Turn / Run 关系，不发布 execution ownership
            # 下一核心入口：_plan_thread_run() 区分 fresh/follow-up/resume，返回本次 claim 所需身份。
            (
                run_request,
                relationship,
                parent_run_id,
                expected_current_run_id,
            ) = self._plan_thread_run(
                run_request,
                repository=conversation_threads,
            )
            events.set_run_context(task=run_request.task)
            # endregion 1. 身份规划结束

            # region 2. 执行环境：先确定真实 workspace，worktree resume 在 claim 前 reattach
            # ownership 之前先准备真实执行环境；worktree resume 也在这里 reattach。
            if self._extensions.execution_environment is None:
                owned_environment = self._build_owned_environment(
                    run_paths.requested_workspace,
                    run_paths.artifact_dir.name,
                    resume_execution_workspace=run_request.resume_execution_workspace,
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

            environment_evidence = environment.probe().to_dict()
            active_workspace_from_probe = environment_evidence.get("active_workspace")
            runtime_workspace = (
                Path(active_workspace_from_probe).resolve()
                if isinstance(active_workspace_from_probe, str)
                else run_paths.requested_workspace
            )
            runtime_config = build_runtime_config(
                self._config,
                run_request,
                workspace=runtime_workspace,
                control_workspace=run_paths.storage_workspace,
                run_dir=run_paths.artifact_dir,
                trace_path=run_paths.trace_file,
                environment=environment,
            )
            # endregion 2. 执行环境结束

            # region 3. Runtime 与稳定输入：claim 前完成唯一装配、冻结或兼容性校验
            # _assemble_agent_loop() 产生后续执行复用的同一组真实 Tool/Skill/Memory 依赖；
            # RunPreparation 随后只构造新快照或纯校验旧快照，不发布 Run ownership。
            tracked_task_states = TrackingTaskStateRepository(
                self._extensions.checkpoint_repository
                or build_task_state_repository(run_paths.task_state_dir)
            )
            runtime_assembly_started = True
            agent_loop = self._assemble_agent_loop(
                runtime_config=runtime_config,
                events=events,
                environment=environment,
                owned_environment=owned_environment,
                conversation_threads=conversation_threads,
                tracked_task_states=tracked_task_states,
            )
            turn_snapshot = conversation_threads.load_turn_snapshot(
                run_request.thread_id,
                run_request.turn_id,
            )
            if relationship == "resume":
                if turn_snapshot is None:  # _plan_thread_run 已 fail closed
                    raise RuntimeError(
                        "cannot resume Turn without durable TurnContextSnapshot"
                    )
                agent_loop.run_preparation.validate_snapshot_contract(
                    turn_snapshot,
                    turn_id=run_request.turn_id,
                    root_task=run_request.task.strip(),
                )
            elif turn_snapshot is None:
                turn_snapshot = agent_loop.run_preparation.build_new_turn_snapshot(
                    turn_id=run_request.turn_id,
                    root_task=run_request.task.strip(),
                )
            else:
                agent_loop.run_preparation.validate_snapshot_contract(
                    turn_snapshot,
                    turn_id=run_request.turn_id,
                    root_task=run_request.task.strip(),
                )
            # endregion 3. Runtime 与稳定输入结束

            # region 4. Durable bootstrap 与 claim：checkpoint 先落盘，Thread 后发布 ownership
            # TaskState.start() 提供 claim 可验证的 durable 起点；_claim_thread_run()
            # 对新 Turn 同锁写 snapshot + turn_start，对 resume 执行 current Run CAS。
            bootstrap = tracked_task_states.start(
                TaskStartRequest(
                    run_id=events.run_id,
                    thread_id=run_request.thread_id,
                    turn_id=run_request.turn_id,
                    workspace=(
                        runtime_config.requested_workspace or runtime_config.workspace
                    ),
                    execution_workspace=runtime_config.workspace,
                    execution_mode=runtime_config.execution_mode,
                    agent_name=run_request.agent_name,
                    context_revision=run_request.context_revision,
                    metadata={"execution_environment": environment_evidence},
                )
            )
            checkpoint_path = str(
                tracked_task_states.path_for(bootstrap.run_id).expanduser().resolve()
            )
            self._claim_thread_run(
                run_request,
                run_id=events.run_id,
                repository=conversation_threads,
                artifact_dir=str(run_paths.artifact_dir.resolve()),
                checkpoint_path=checkpoint_path,
                relationship=relationship,
                parent_run_id=parent_run_id,
                expected_current_run_id=expected_current_run_id,
                started_at=started_at,
                snapshot=(turn_snapshot if relationship != "resume" else None),
                expected_context_revision=run_request.context_revision,
            )
            # endregion 4. Durable bootstrap 与 claim结束
        except Exception as exc:
            # checkpoint -> claim 失败只产生未进入 Thread 的 orphan。仅当 durable
            # Thread ownership 确认不存在时，回收本次 worktree/artifact；若 claim
            # 的 metadata 写入已落盘但调用方收到迟到异常，则保留可恢复状态。
            assembly_failed = runtime_assembly_started and agent_loop is None
            if assembly_failed:
                finalize_run_artifacts(
                    request=run_request,
                    paths=run_paths,
                    events=events,
                    uses_default_trace=uses_default_trace,
                    owned_environment=owned_environment,
                    owned_environment_is_prepared=owned_environment_is_prepared,
                    result=None,
                    failure_stop_reason=f"exception:{type(exc).__name__}",
                )
            close_events = getattr(events, "close", None)
            if callable(close_events):
                close_events()
            claim_state = self._run_claim_state(
                conversation_threads,
                run_id=events.run_id,
                thread_id=run_request.thread_id,
            )
            if claim_state is False:
                if (
                    not assembly_failed
                    and owned_environment is not None
                    and owned_environment_is_prepared
                ):
                    owned_environment.cleanup()
                if checkpoint_path:
                    orphan_checkpoint = Path(checkpoint_path).resolve()
                    if not orphan_checkpoint.is_relative_to(
                        run_paths.artifact_dir.resolve()
                    ):
                        orphan_checkpoint.unlink(missing_ok=True)
                if not assembly_failed:
                    shutil.rmtree(run_paths.artifact_dir)
            raise

        # 执行状态（仅供 finally 收口）：owned 表示由 Harness 创建并负责清理。
        run_result: RunResult | None = None
        failure_stop_reason = ""
        preserve_execution_workspace = False
        # endregion 准备区结束
        # region 2. 执行环境与 Runtime：约束外部状态变化，再进入唯一 AgentLoop
        try:
            # 主执行区：环境、Runtime 与 ownership 已准备完成，只进入一次 AgentLoop。
            assert runtime_config is not None
            assert tracked_task_states is not None
            assert agent_loop is not None
            run_result = self._execute_run(
                run_request,
                run_paths,
                events,
                owned_environment,
                environment_evidence,
                tracked_task_states,
                agent_loop,
            )
        except Exception as exc:
            failure_stop_reason = f"exception:{type(exc).__name__}"
            failure_checkpoint = tracked_task_states.latest
            if failure_checkpoint is None:
                try:
                    failure_checkpoint = tracked_task_states.load_path(checkpoint_path)
                except (FileNotFoundError, OSError, ValueError):
                    failure_checkpoint = None
            if (
                failure_checkpoint is not None
                and failure_checkpoint.status == TaskRunStatus.CREATED.value
            ):
                failure_checkpoint = tracked_task_states.update(
                    failure_checkpoint,
                    TaskCheckpointUpdate(
                        status=TaskRunStatus.FAILED,
                        stop_reason=failure_stop_reason,
                    ),
                )
            if (
                failure_checkpoint is not None
                and failure_checkpoint.status in RESUMABLE_RUN_STATUSES
            ):
                preserve_execution_workspace = True
                failure_status = failure_checkpoint.status
                failure_stop_reason = (
                    failure_checkpoint.stop_reason or failure_stop_reason
                )
            else:
                failure_status = (
                    failure_checkpoint.status
                    if failure_checkpoint is not None
                    else TaskRunStatus.FAILED.value
                )
            conversation_threads.record_run(
                run_request.thread_id,
                run_request.turn_id,
                ThreadRun(
                    run_id=events.run_id,
                    artifact_dir=str(run_paths.artifact_dir.resolve()),
                    checkpoint_path=checkpoint_path,
                    status=failure_status,
                    relationship=relationship,
                    parent_run_id=parent_run_id,
                    stop_reason=failure_stop_reason,
                    current_step=(
                        failure_checkpoint.current_step
                        if failure_checkpoint is not None
                        else 0
                    ),
                    created_at=(
                        failure_checkpoint.created_at
                        if failure_checkpoint is not None
                        else started_at
                    ),
                    updated_at=time.time(),
                ),
            )
            if failure_status not in RESUMABLE_RUN_STATUSES:
                conversation_threads.finish_turn(
                    run_request.thread_id,
                    run_request.turn_id,
                    failure_status,
                    run_id=events.run_id,
                )
            raise
        finally:
            try:
                finalize_run_artifacts(
                    request=run_request,
                    paths=run_paths,
                    events=events,
                    uses_default_trace=uses_default_trace,
                    owned_environment=owned_environment,
                    owned_environment_is_prepared=owned_environment_is_prepared,
                    result=run_result,
                    failure_stop_reason=failure_stop_reason,
                    preserve_execution_workspace=preserve_execution_workspace,
                )
                if run_result is not None:
                    conversation_threads.record_run(
                        run_request.thread_id,
                        run_request.turn_id,
                        ThreadRun(
                            run_id=run_result.run_id,
                            artifact_dir=str(run_result.artifact_dir.resolve()),
                            checkpoint_path=checkpoint_path,
                            status=run_result.status.value,
                            relationship=relationship,
                            parent_run_id=parent_run_id,
                            stop_reason=run_result.stop_reason,
                            current_step=run_result.checkpoint.current_step,
                            created_at=run_result.checkpoint.created_at,
                            updated_at=run_result.checkpoint.updated_at,
                        ),
                    )
            finally:
                # 只发布原生 run 的发现指针，不再复制或重组运行证据。
                write_latest_run_pointer(
                    run_paths.storage_workspace,
                    run_paths.artifact_dir,
                )
        # endregion 2. 执行环境与 Runtime结束

        # region 3. Public API 收口：导航已发布，只拒绝没有类型化结果的异常状态
        if run_result is None:
            raise RuntimeError("Harness run ended without a typed result")
        return run_result
        # endregion 3. Public API 收口结束

    # region Runtime 装配细节
    def _execute_run(
        self,
        request: RunRequest,
        run_paths: HarnessRunPaths,
        events: EventSink,
        owned_environment: ExecutionEnvironment | None,
        environment_evidence: JsonObject,
        tracked_task_states: TrackingTaskStateRepository,
        agent_loop: AgentLoop,
    ) -> RunResult:
        """执行 claim 前已装配的唯一 AgentLoop，并构造 Public API 结果。"""

        # region 1. 运行证据：在唯一 AgentLoop 前发布环境与脱敏请求
        # events.add() 固化实际 execution workspace；request artifact 只保存公开配置，
        # 不复制 RuntimeDependencies 或 snapshot 内容。
        events.add(
            0,
            "Runtime",
            "execution_environment",
            execution_environment=environment_evidence,
        )
        write_request_artifact(run_paths.artifact_dir, request, self._config)
        # endregion 1. 运行证据结束

        # region 2. 规范执行：复用 claim 前装配的唯一 AgentLoop
        # agent_loop.run() 是唯一模型/工具入口；返回后才读取 owned workspace Diff，
        # 避免预执行候选状态被误当作本 Run 产物。
        stop_output = agent_loop.run(agent_name=request.agent_name)
        if owned_environment is not None:
            run_paths.candidate_diff_file.write_text(
                owned_environment.diff(),
                encoding="utf-8",
            )
        # endregion 2. 规范执行结束

        # region 3. 结果收口：只从最新 durable checkpoint 构造对外 RunResult
        # AgentLoop 的字符串返回值不是状态真相。对外 status/stop_reason 必须读取
        # RunLifecycle 最后持久化的 checkpoint，避免文本“完成”覆盖真实阻断状态。
        final_checkpoint = tracked_task_states.latest
        if final_checkpoint is None:
            raise RuntimeError("AgentLoop completed without creating a checkpoint")
        if final_checkpoint.stop_output != stop_output:
            raise RuntimeError(
                "AgentLoop stop output does not match durable checkpoint"
            )
        run_paths.stop_output_file.write_text(stop_output, encoding="utf-8")
        if final_checkpoint.final_answer is not None:
            run_paths.final_answer_file.write_text(
                final_checkpoint.final_answer,
                encoding="utf-8",
            )
        final_status = TaskRunStatus(final_checkpoint.status)
        uses_default_trace = self._extensions.event_sink_factory is None
        return RunResult(
            run_id=events.run_id,
            thread_id=request.thread_id,
            turn_id=request.turn_id,
            status=final_status,
            stop_reason=final_checkpoint.stop_reason,
            stop_output=stop_output,
            final_answer=final_checkpoint.final_answer,
            artifact_dir=run_paths.artifact_dir,
            checkpoint=final_checkpoint,
            trace_path=run_paths.trace_file if uses_default_trace else None,
            usage_path=(
                run_paths.artifact_dir / "usage.json" if uses_default_trace else None
            ),
            candidate_diff_path=(
                run_paths.candidate_diff_file if owned_environment is not None else None
            ),
            manifest_path=run_paths.manifest_file,
        )
        # endregion 3. 结果收口结束

    def _assemble_agent_loop(
        self,
        *,
        runtime_config: RuntimeConfig,
        events: EventSink,
        environment: EnvironmentPort,
        owned_environment: ExecutionEnvironment | None,
        conversation_threads: ConversationThreadRepository,
        tracked_task_states: TrackingTaskStateRepository,
    ) -> AgentLoop:
        """在 claim 前一次性装配稳定输入与执行共用的真实 Runtime。"""

        runtime_workspace = Path(runtime_config.workspace).resolve()
        tool_gateway = self._tools or build_registry(
            ToolRegistryBuildRequest(
                workspace=str(runtime_workspace),
                auto=True,
                enabled_tools=self._config.enabled_tools,
                mcp_config_file=self._config.mcp_config_file,
                mcp_allowed_tools=self._config.mcp_allowed_tools,
                execution_environment=owned_environment,
                memory_root=runtime_config.memory_root,
                memory_namespace=runtime_config.memory_namespace,
            )
        )
        return build_agent_loop_from_request(
            AgentLoopBuildRequest(
                config=runtime_config,
                trace=events,
                registry=tool_gateway,
                llm=self._model,
                overrides=RuntimeDependencyOverrides(
                    turn_system_context_assembler=(
                        self._extensions.turn_system_context_assembler
                    ),
                    skills=self._extensions.skill_selector,
                    environment=environment,
                    hooks=self._extensions.hook_policy,
                    additional_hooks=self._extensions.lifecycle_hooks,
                    task_states=tracked_task_states,
                    conversation_threads=conversation_threads,
                    approvals=self._extensions.approval_repository,
                    human_inputs=self._extensions.human_input_repository,
                    operations=self._extensions.operation_repository,
                    long_term_memory_recall=self._extensions.long_term_memory_recall,
                    control=self._extensions.run_control,
                ),
            )
        )
    def _build_owned_environment(
        self,
        requested_workspace: Path,
        run_id: str,
        resume_execution_workspace: str = "",
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
                reattach_workspace=resume_execution_workspace,
            )
        )

    def _conversation_repository(
        self,
        storage_workspace: Path,
    ) -> ConversationThreadRepository:
        if self._extensions.conversation_threads is not None:
            return self._extensions.conversation_threads
        root = control_path(
            self._config.conversation_thread_root,
            storage_workspace,
            "threads",
        )
        return build_conversation_thread_repository(root)

    @staticmethod
    def _run_claim_state(
        repository: ConversationThreadRepository,
        *,
        run_id: str,
        thread_id: str,
    ) -> bool | None:
        """确认 bind 异常前 Run 是否已 durable claim；读取失败时返回 unknown。"""

        try:
            if thread_id:
                thread = repository.get(thread_id)
                threads = [] if thread is None else [thread]
            else:
                threads = repository.list_all()
        except Exception:
            return None
        return any(run.run_id == run_id for thread in threads for run in thread.runs)

    def _plan_thread_run(
        self,
        request: RunRequest,
        *,
        repository: ConversationThreadRepository,
    ) -> tuple[RunRequest, str, str, str]:
        """解析 Thread/Turn 身份；此阶段绝不发布 active Run ownership。"""

        requested_workspace = Path(
            request.workspace or self._config.workspace
        ).expanduser().resolve()
        # region 1. Resume：同 Thread、同 Turn、新 Run；禁止覆盖 root task / workspace / context
        if request.resume_state:
            restored = (
                self._extensions.checkpoint_repository.load_path(request.resume_state)
                if self._extensions.checkpoint_repository is not None
                else load_task_checkpoint(request.resume_state)
            )
            if restored.status not in RESUMABLE_RUN_STATUSES:
                raise RuntimeError(f"cannot resume terminal Run: {restored.status}")
            if request.thread_id and request.thread_id != restored.thread_id:
                raise ValueError("resume thread_id does not match checkpoint")
            if request.turn_id and request.turn_id != restored.turn_id:
                raise ValueError("resume turn_id does not match checkpoint")
            thread = repository.get(restored.thread_id)
            if thread is None:
                raise RuntimeError(f"checkpoint Thread does not exist: {restored.thread_id}")
            if requested_workspace != Path(thread.workspace).resolve():
                raise ValueError("resume workspace does not match ConversationThread")
            turn = thread.require_turn(restored.turn_id)
            if not turn.is_active:
                raise RuntimeError(f"cannot resume terminal Turn: {turn.turn_id}")
            if request.task.strip() != turn.root_task:
                raise ValueError("resume cannot override Turn.root_task")
            if request.context_revision != restored.context_revision:
                raise ValueError("resume context_revision does not match checkpoint")
            if (
                request.resume_execution_workspace
                and Path(request.resume_execution_workspace).resolve()
                != Path(restored.execution_workspace).resolve()
            ):
                raise ValueError("resume execution workspace does not match checkpoint")

            # Resume 继续的是原 Turn，不能在发布新 Run ownership 后才发现稳定规则丢失。
            # 缺少快照时直接拒绝，避免按当前 AGENTS/Skill/Memory 静默重建另一套契约。
            if repository.load_turn_snapshot(thread.thread_id, turn.turn_id) is None:
                raise RuntimeError(
                    "cannot resume Turn without durable TurnContextSnapshot"
                )
            return (
                replace(
                    request,
                    thread_id=restored.thread_id,
                    turn_id=restored.turn_id,
                    context_revision=restored.context_revision,
                    resume_execution_workspace=restored.execution_workspace,
                ),
                "resume",
                restored.run_id,
                restored.run_id,
            )
        # endregion 1. Resume 原 Turn结束

        # region 2. Fresh / Follow-up：同一用户新输入只能创建新的 Turn
        if request.context_revision:
            raise ValueError("fresh/new-turn request cannot set context_revision")
        if request.resume_execution_workspace:
            raise ValueError("fresh/new-turn request cannot set execution workspace")
        thread_id = request.thread_id or f"thread-{uuid.uuid4().hex[:12]}"
        thread = repository.get(thread_id)
        now = time.time()
        relationship = "follow_up"
        parent_run_id = ""
        if thread is None:
            thread = repository.create(
                ConversationThread(
                    thread_id=thread_id,
                    title=self._thread_title(request.task),
                    initial_task=request.task.strip(),
                    workspace=str(requested_workspace),
                    created_at=now,
                    updated_at=now,
                )
            )
            relationship = "initial"
        else:
            if Path(thread.workspace).resolve() != requested_workspace:
                raise ValueError("request workspace does not match ConversationThread")
            if thread.active_turn_id:
                raise RuntimeError(
                    f"thread already has active Turn: {thread.active_turn_id}; resume it first"
                )
            previous_runs = [run for turn in thread.turns for run in turn.runs]
            if previous_runs:
                parent_run_id = max(
                    previous_runs,
                    key=lambda item: (item.updated_at, item.run_id),
                ).run_id
            else:
                # Operator Console 可以先创建空 Thread；首个 Turn 仍是 initial。
                relationship = "initial"

        turn_id = request.turn_id or f"turn-{uuid.uuid4().hex[:12]}"
        if any(item.turn_id == turn_id for item in thread.turns):
            raise ValueError(f"fresh request cannot reuse Turn id: {turn_id}")
        context_state = repository.load_context_state(thread_id)
        context_revision = context_state.revision if context_state is not None else 0
        return (
            replace(
                request,
                workspace=str(requested_workspace),
                thread_id=thread_id,
                turn_id=turn_id,
                context_revision=context_revision,
            ),
            relationship,
            parent_run_id,
            "",
        )
        # endregion 2. Fresh / Follow-up结束

    @staticmethod
    def _claim_thread_run(
        request: RunRequest,
        *,
        run_id: str,
        repository: ConversationThreadRepository,
        artifact_dir: str,
        checkpoint_path: str,
        relationship: str,
        parent_run_id: str,
        expected_current_run_id: str,
        started_at: float,
        snapshot: TurnContextSnapshot | None,
        expected_context_revision: int,
    ) -> None:
        """在 bootstrap checkpoint durable 后，原子发布本 Run ownership。"""

        run = ThreadRun(
            run_id=run_id,
            artifact_dir=artifact_dir,
            checkpoint_path=checkpoint_path,
            status=TaskRunStatus.CREATED.value,
            relationship=relationship,
            parent_run_id=parent_run_id,
            created_at=started_at,
            updated_at=started_at,
        )
        if relationship == "resume":
            repository.claim_resume_run(
                request.thread_id,
                request.turn_id,
                expected_current_run_id=expected_current_run_id,
                run=run,
            )
            return
        repository.start_turn(
            request.thread_id,
            Turn(
                turn_id=request.turn_id,
                root_task=request.task.strip(),
                input_item_id=f"user:{request.turn_id}",
                status="active",
                created_at=started_at,
                updated_at=started_at,
            ),
            ConversationItemDraft(
                item_id=f"user:{request.turn_id}",
                turn_id=request.turn_id,
                run_id=run_id,
                role="user",
                content=request.task.strip(),
                origin="human",
                human_authority=True,
            ),
            run,
            snapshot=snapshot,
            expected_context_revision=expected_context_revision,
        )

    @staticmethod
    def _thread_title(task: str) -> str:
        normalized = re.sub(r"\s+", " ", task).strip()
        if not normalized:
            return "Untitled Thread"
        return normalized if len(normalized) <= 48 else f"{normalized[:47]}…"

    # endregion Runtime 装配细节结束

    # 主要入口：从 durable checkpoint 创建一次显式 continuation。
    def resume(
        self,
        checkpoint_path: str | Path,
    ) -> RunResult:
        """加载 checkpoint，并用新的 run 继续，不声称恢复隐藏模型状态。"""

        resume_checkpoint_path = str(checkpoint_path)
        checkpoint_repository = self._extensions.checkpoint_repository
        restored_checkpoint = (
            checkpoint_repository.load_path(resume_checkpoint_path)
            if checkpoint_repository is not None
            else load_task_checkpoint(resume_checkpoint_path)
        )
        if restored_checkpoint.status not in RESUMABLE_RUN_STATUSES:
            raise RuntimeError(
                f"cannot resume terminal Run: {restored_checkpoint.status}"
            )
        repository = self._conversation_repository(
            self._resume_storage_workspace(
                resume_checkpoint_path,
                Path(restored_checkpoint.workspace).resolve(),
            )
        )
        thread = repository.get(restored_checkpoint.thread_id)
        if thread is None:
            raise RuntimeError(
                f"checkpoint Thread does not exist: {restored_checkpoint.thread_id}"
            )
        if Path(thread.workspace).resolve() != Path(restored_checkpoint.workspace).resolve():
            raise RuntimeError("checkpoint workspace does not match ConversationThread")
        turn = thread.require_turn(restored_checkpoint.turn_id)
        if not turn.is_active:
            raise RuntimeError(f"cannot resume terminal Turn: {turn.turn_id}")
        if restored_checkpoint.execution_mode == "container":
            raise RuntimeError(
                "container execution resume is not supported without durable snapshot reattach"
            )
        return self.run(
            RunRequest(
                task=turn.root_task,
                workspace=thread.workspace,
                agent_name=restored_checkpoint.agent_name,
                resume_state=resume_checkpoint_path,
                thread_id=thread.thread_id,
                turn_id=turn.turn_id,
                context_revision=restored_checkpoint.context_revision,
                resume_execution_workspace=restored_checkpoint.execution_workspace,
                run_label=f"continuation-{restored_checkpoint.agent_name}",
            )
        )

    @staticmethod
    def _resume_storage_workspace(
        checkpoint_path: str | Path,
        requested_workspace: Path,
    ) -> Path:
        """从 managed ``.agent_forge`` artifact 恢复原控制面 owner。"""

        resolved = Path(checkpoint_path).expanduser().resolve()
        for parent in resolved.parents:
            if parent.name == ".agent_forge":
                return parent.parent
        return requested_workspace


__all__ = [
    "Harness",
    "HarnessConfig",
    "HarnessExtensions",
    "EventSinkFactory",
    "RunRequest",
    "RunResult",
]
