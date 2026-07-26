"""Operator Console 的装配入口。"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from pathlib import Path

from agent_forge._harness_support import control_path
from agent_forge.cli.repository import (
    build_single_harness,
    build_single_run_request,
    resolve_repository_arguments,
)
from agent_forge.control import RunController
from agent_forge.harness import HarnessExtensions, RunRequest
from agent_forge.observability.adapters.streaming import EventStreamPolicy
from agent_forge.operator_console.events import RuntimeEventBuffer
from agent_forge.operator_console.session import OperatorSession
from agent_forge.runtime.api import (
    build_approval_repository,
    build_human_input_repository,
)


@dataclass(frozen=True)
class OperatorSessionBundle:
    """Textual 界面启动一次真实运行所需的全部对象。"""

    session: OperatorSession
    events: RuntimeEventBuffer
    request: RunRequest


def build_operator_session(
    base_args: argparse.Namespace,
    *,
    task: str,
    workspace: str,
) -> OperatorSessionBundle:
    """复用 CLI 的配置优先级和 Harness 装配，创建前台操作会话。"""

    args = copy.deepcopy(base_args)
    args.task = task
    args.workspace = workspace
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
    )
    return OperatorSessionBundle(session=session, events=events, request=request)


# 主要入口：由 ``forge console`` 启动本地交互操作台。
def run_console_from_args(args: argparse.Namespace) -> None:
    """延迟导入 Textual，避免展示层影响 headless Harness 导入。"""

    try:
        from agent_forge.operator_console.app import OperatorConsoleApp
    except ModuleNotFoundError as exc:
        if exc.name == "textual":
            raise SystemExit(
                "Operator Console 需要 Textual；请重新安装当前项目依赖。"
            ) from exc
        raise
    OperatorConsoleApp(args).run()
