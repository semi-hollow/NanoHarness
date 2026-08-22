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
from apps.operator_console.adapters import JsonTaskSessionCatalog
from apps.operator_console.application import TaskSessionLibrary
from apps.operator_console.events import RuntimeEventBuffer
from apps.operator_console.session import OperatorSession
from agent_forge.runtime.api import (
    build_approval_repository,
    build_human_input_repository,
)
from agent_forge.infrastructure.storage_layout import MEMORY_ROOT, SESSIONS_ROOT


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
    task_session_id: str = ""


def build_task_session_library(base_args: argparse.Namespace) -> TaskSessionLibrary:
    """按 output root 构造项目级会话目录；Run artifact 仍放在原目录。"""

    workspace = Path(getattr(base_args, "workspace", ".") or ".").expanduser()
    catalog_root = workspace.resolve() / SESSIONS_ROOT
    return TaskSessionLibrary(JsonTaskSessionCatalog(catalog_root))


def build_operator_session(
    base_args: argparse.Namespace,
    *,
    task: str,
    workspace: str,
    task_session_id: str = "",
    session_title: str = "",
    task_sessions: TaskSessionLibrary | None = None,
) -> OperatorSessionBundle:
    """复用 CLI 的配置优先级和 Harness 装配，创建前台操作会话。"""

    args = copy.deepcopy(base_args)
    session_library = task_sessions or build_task_session_library(args)
    task_session = (
        session_library.require(task_session_id)
        if task_session_id
        else session_library.create(
            task=task,
            workspace=workspace,
            title=session_title,
        )
    )
    args.task = task
    args.workspace = task_session.workspace
    args.human_thread_id = task_session.human_thread_id
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
        run_control=controller,
    )
    request = build_single_run_request(args, config_document)
    session = OperatorSession(
        harness=build_single_harness(args, extensions=extensions),
        request=request,
        controller=controller,
        approvals=approvals,
        human_inputs=human_inputs,
        task_sessions=session_library,
        task_session_id=task_session.session_id,
    )
    return OperatorSessionBundle(
        session=session,
        events=events,
        request=request,
        task_session_id=task_session.session_id,
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
    session_library = build_task_session_library(args)
    output_root = getattr(args, "output_root", "") or ".agent_forge/runs"
    session_library.import_existing_runs(output_root)
    OperatorConsoleApp(args, task_sessions=session_library).run()
