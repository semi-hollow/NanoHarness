"""Operator Console 的装配入口。"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from pathlib import Path

from agent_forge._harness_support import control_path
from apps.run_composition import (
    build_single_harness,
    build_single_run_request,
    resolve_repository_arguments,
)
from agent_forge.control import RunController
from agent_forge.harness import HarnessExtensions, RunRequest
from agent_forge.observability.adapters.streaming import EventStreamPolicy
from agent_forge.runtime.adapters.thread_json import JsonConversationThreadRepository
from apps.operator_console.application import ConversationThreadLibrary
from apps.operator_console.events import RuntimeEventBuffer
from apps.operator_console.session import OperatorSession
from agent_forge.runtime.api import (
    build_approval_repository,
    build_human_input_repository,
)
from agent_forge.infrastructure.storage_layout import MEMORY_ROOT, THREADS_ROOT


def resolve_operator_memory_root(
    base_args: argparse.Namespace,
    workspace: str,
) -> Path:
    """解析 Operator Console 与 Runtime 共用的长期记忆目录。"""

    configured_root = getattr(base_args, "memory_root", None)
    if configured_root is None and getattr(base_args, "config", None):
        from apps.run_configuration import load_run_config

        config_document = load_run_config(base_args.config)
        configured_root = config_document.values.get("memory_root")
    return control_path(
        str(configured_root or MEMORY_ROOT),
        Path(workspace).expanduser().resolve(),
        "memory",
    )


@dataclass(frozen=True)
class OperatorSessionBundle:
    """Textual 界面启动一次真实运行所需的全部对象。"""

    session: OperatorSession
    events: RuntimeEventBuffer
    request: RunRequest
    thread_id: str = ""


def build_conversation_thread_library(
    base_args: argparse.Namespace,
) -> ConversationThreadLibrary:
    """构造 Console 与 Harness 共用的 canonical Thread Repository。"""

    workspace = Path(getattr(base_args, "workspace", ".") or ".").expanduser()
    return ConversationThreadLibrary(
        JsonConversationThreadRepository(workspace.resolve() / THREADS_ROOT)
    )


def build_operator_session(
    base_args: argparse.Namespace,
    *,
    task: str,
    workspace: str,
    thread_id: str = "",
    session_title: str = "",
    threads: ConversationThreadLibrary | None = None,
) -> OperatorSessionBundle:
    """复用 CLI 的配置优先级和 Harness 装配，创建前台操作会话。"""

    args = copy.deepcopy(base_args)
    thread_library = threads or build_conversation_thread_library(args)
    thread = (
        thread_library.require(thread_id)
        if thread_id
        else thread_library.create(
            task=task,
            workspace=workspace,
            title=session_title,
        )
    )
    args.task = task
    args.workspace = thread.workspace
    args.thread_id = thread.thread_id
    args.agent_mode = "single"
    config_document = resolve_repository_arguments(args)

    requested_workspace = Path(args.workspace).expanduser().resolve()
    approvals = build_approval_repository(
        control_path(args.approval_root, requested_workspace, "approvals")
    )
    human_inputs = build_human_input_repository(
        control_path(args.human_input_root, requested_workspace, "human_input")
    )
    controller = RunController()
    events = RuntimeEventBuffer()
    extensions = HarnessExtensions(
        event_listeners=(events,),
        event_stream_policy=EventStreamPolicy(
            include_sensitive_data=True,
            max_text_chars=1_200,
        ),
        approval_repository=approvals,
        human_input_repository=human_inputs,
        conversation_threads=thread_library.repository,
        run_control=controller,
    )
    request = build_single_run_request(args, config_document)
    session = OperatorSession(
        harness=build_single_harness(args, extensions=extensions),
        request=request,
        controller=controller,
        approvals=approvals,
        human_inputs=human_inputs,
        thread_library=thread_library,
        thread_id=thread.thread_id,
    )
    return OperatorSessionBundle(
        session=session,
        events=events,
        request=request,
        thread_id=thread.thread_id,
    )


# 主要入口：由 ``forge console`` 启动本地交互操作台。
def run_console_from_args(args: argparse.Namespace) -> None:
    """延迟导入 Textual，避免展示层影响 headless Harness 导入。"""

    try:
        from apps.operator_console.app import OperatorConsoleApp
    except ModuleNotFoundError as exc:
        if exc.name == "textual":
            raise SystemExit(
                "Operator Console 需要 Textual；请重新安装当前项目依赖。"
            ) from exc
        raise
    thread_library = build_conversation_thread_library(args)
    OperatorConsoleApp(args, threads=thread_library).run()
