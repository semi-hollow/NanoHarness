"""Direct AgentLoop tests 的 canonical Thread/Turn fixture。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from agent_forge.runtime.adapters.thread_json import JsonConversationThreadRepository
from agent_forge.runtime.adapters.task_state_json import JsonTaskStateRepository
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.task import TaskRunStatus, TaskStartRequest
from agent_forge.runtime.domain.thread import (
    ConversationItemDraft,
    ConversationThread,
    ThreadRun,
    Turn,
)
from agent_forge.runtime.ports import EventSink


@dataclass(frozen=True)
class RuntimeThreadFixture:
    """测试 Run 的 canonical 身份和 durable checkpoint 位置。"""

    config: RuntimeConfig
    repository: JsonConversationThreadRepository
    thread_id: str
    turn_id: str
    run_id: str
    task: str

    @property
    def checkpoint_path(self) -> Path:
        return Path(self.config.task_state_root) / f"{self.run_id}.json"


def bind_new_runtime_turn(
    config: RuntimeConfig,
    trace: EventSink,
    task: str,
    *,
    agent_name: str = "CodingAgent",
) -> RuntimeThreadFixture:
    """为 direct AgentLoop test 创建 user Thread、Turn 和 initial Run。"""

    workspace = Path(config.requested_workspace or config.workspace).resolve()
    state_root = workspace / ".test_runtime"
    thread_root = state_root / "threads"
    task_state_root = state_root / "task_state"
    repository = JsonConversationThreadRepository(thread_root)
    now = time.time()
    thread_id = f"thread-{uuid.uuid4().hex[:12]}"
    turn_id = f"turn-{uuid.uuid4().hex[:12]}"
    input_item_id = f"user:{turn_id}"
    runtime_config = _bind_config(
        config,
        workspace=workspace,
        state_root=state_root,
        thread_root=thread_root,
        task_state_root=task_state_root,
        thread_id=thread_id,
        turn_id=turn_id,
    )
    repository.create(
        ConversationThread(
            thread_id=thread_id,
            thread_kind="user",
            title=task,
            initial_task=task,
            workspace=str(workspace),
            created_at=now,
            updated_at=now,
        )
    )
    _create_bootstrap(
        runtime_config,
        trace.run_id,
        thread_id,
        turn_id,
        agent_name=agent_name,
    )
    repository.start_turn(
        thread_id,
        Turn(
            turn_id=turn_id,
            root_task=task,
            input_item_id=input_item_id,
            status=TaskRunStatus.RUNNING.value,
            created_at=now,
            updated_at=now,
        ),
        ConversationItemDraft(
            item_id=input_item_id,
            turn_id=turn_id,
            run_id=trace.run_id,
            role="user",
            content=task,
            origin="human",
            human_authority=True,
        ),
        _thread_run(
            runtime_config,
            run_id=trace.run_id,
            relationship="initial",
            created_at=now,
        ),
    )
    return RuntimeThreadFixture(
        config=runtime_config,
        repository=repository,
        thread_id=thread_id,
        turn_id=turn_id,
        run_id=trace.run_id,
        task=task,
    )


def bind_resume_runtime_turn(
    config: RuntimeConfig,
    trace: EventSink,
    previous: RuntimeThreadFixture,
) -> RuntimeThreadFixture:
    """把新 Run 绑定到同一 active Turn，并指向上一 Run checkpoint。"""

    now = time.time()
    runtime_config = replace(
        config,
        workspace=previous.config.workspace,
        requested_workspace=previous.config.requested_workspace,
        execution_mode=previous.config.execution_mode,
        thread_id=previous.thread_id,
        turn_id=previous.turn_id,
        context_revision=previous.config.context_revision,
        conversation_thread_root=previous.config.conversation_thread_root,
        task_state_root=previous.config.task_state_root,
        approval_root=previous.config.approval_root,
        human_input_root=previous.config.human_input_root,
        operation_ledger_root=previous.config.operation_ledger_root,
        memory_root=previous.config.memory_root,
        resume_state=str(previous.checkpoint_path),
    )
    _create_bootstrap(
        runtime_config,
        trace.run_id,
        previous.thread_id,
        previous.turn_id,
    )
    previous.repository.claim_resume_run(
        previous.thread_id,
        previous.turn_id,
        expected_current_run_id=previous.run_id,
        run=_thread_run(
            runtime_config,
            run_id=trace.run_id,
            relationship="resume",
            parent_run_id=previous.run_id,
            created_at=now,
        ),
    )
    return RuntimeThreadFixture(
        config=runtime_config,
        repository=previous.repository,
        thread_id=previous.thread_id,
        turn_id=previous.turn_id,
        run_id=trace.run_id,
        task=previous.task,
    )


def bind_follow_up_runtime_turn(
    config: RuntimeConfig,
    trace: EventSink,
    previous: RuntimeThreadFixture,
    task: str | None = None,
) -> RuntimeThreadFixture:
    """在已终态 Thread 下为普通后续请求创建新 Turn，而不是伪装 resume。"""

    next_task = task or previous.task
    now = time.time()
    turn_id = f"turn-{uuid.uuid4().hex[:12]}"
    input_item_id = f"user:{turn_id}"
    context_state = previous.repository.load_context_state(previous.thread_id)
    runtime_config = replace(
        config,
        workspace=previous.config.workspace,
        requested_workspace=previous.config.requested_workspace,
        execution_mode=previous.config.execution_mode,
        thread_id=previous.thread_id,
        turn_id=turn_id,
        context_revision=context_state.revision if context_state is not None else 0,
        conversation_thread_root=previous.config.conversation_thread_root,
        task_state_root=previous.config.task_state_root,
        approval_root=previous.config.approval_root,
        human_input_root=previous.config.human_input_root,
        operation_ledger_root=previous.config.operation_ledger_root,
        memory_root=previous.config.memory_root,
        resume_state="",
    )
    _create_bootstrap(
        runtime_config,
        trace.run_id,
        previous.thread_id,
        turn_id,
    )
    previous.repository.start_turn(
        previous.thread_id,
        Turn(
            turn_id=turn_id,
            root_task=next_task,
            input_item_id=input_item_id,
            status=TaskRunStatus.RUNNING.value,
            created_at=now,
            updated_at=now,
        ),
        ConversationItemDraft(
            item_id=input_item_id,
            turn_id=turn_id,
            run_id=trace.run_id,
            role="user",
            content=next_task,
            origin="human",
            human_authority=True,
        ),
        _thread_run(
            runtime_config,
            run_id=trace.run_id,
            relationship="follow_up",
            parent_run_id=previous.run_id,
            created_at=now,
        ),
    )
    return RuntimeThreadFixture(
        config=runtime_config,
        repository=previous.repository,
        thread_id=previous.thread_id,
        turn_id=turn_id,
        run_id=trace.run_id,
        task=next_task,
    )


def _bind_config(
    config: RuntimeConfig,
    *,
    workspace: Path,
    state_root: Path,
    thread_root: Path,
    task_state_root: Path,
    thread_id: str,
    turn_id: str,
) -> RuntimeConfig:
    return replace(
        config,
        workspace=str(workspace),
        requested_workspace=str(workspace),
        execution_mode="local",
        thread_id=thread_id,
        turn_id=turn_id,
        context_revision=0,
        conversation_thread_root=str(thread_root),
        task_state_root=str(task_state_root),
        approval_root=_isolated_root(config.approval_root, state_root / "approvals"),
        human_input_root=_isolated_root(
            config.human_input_root,
            state_root / "human_input",
        ),
        operation_ledger_root=_isolated_root(
            config.operation_ledger_root,
            state_root / "operation_ledger",
        ),
        memory_root=_isolated_root(config.memory_root, state_root / "memory"),
    )


def _thread_run(
    config: RuntimeConfig,
    *,
    run_id: str,
    relationship: str,
    created_at: float,
    parent_run_id: str = "",
) -> ThreadRun:
    return ThreadRun(
        run_id=run_id,
        artifact_dir=str(Path(config.task_state_root).parent / "runs" / run_id),
        checkpoint_path=str(Path(config.task_state_root) / f"{run_id}.json"),
        status=TaskRunStatus.CREATED.value,
        relationship=relationship,
        parent_run_id=parent_run_id,
        created_at=created_at,
        updated_at=created_at,
    )


def _create_bootstrap(
    config: RuntimeConfig,
    run_id: str,
    thread_id: str,
    turn_id: str,
    *,
    agent_name: str = "CodingAgent",
) -> None:
    JsonTaskStateRepository(config.task_state_root).start(
        TaskStartRequest(
            run_id=run_id,
            thread_id=thread_id,
            turn_id=turn_id,
            workspace=config.requested_workspace or config.workspace,
            execution_workspace=config.workspace,
            execution_mode=config.execution_mode,
            agent_name=agent_name,
            context_revision=config.context_revision,
        )
    )


def _isolated_root(value: str, fallback: Path) -> str:
    path = Path(value) if value else fallback
    return str(path if path.is_absolute() else fallback)


__all__ = [
    "RuntimeThreadFixture",
    "bind_follow_up_runtime_turn",
    "bind_new_runtime_turn",
    "bind_resume_runtime_turn",
]
