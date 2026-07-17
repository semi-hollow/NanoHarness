"""审批与人工回答命令；只调用 Runtime 公共 API。"""

from __future__ import annotations

import argparse

from agent_forge.runtime.api import (
    decide_approval,
    respond_to_human_input as persist_human_input_response,
)

# 主要入口：保存副作用批准/拒绝决定；不会在本命令中执行工具。
def approve_request(args: argparse.Namespace) -> str:
    """保存批准或拒绝；该命令本身不执行工具。"""

    request = decide_approval(
        args.approval_root,
        args.operation_key,
        args.decision,
        note=getattr(args, "note", ""),
    )
    return (
        f"approval {request.status}: operation_key={request.operation_key} "
        f"tool={request.tool_name} path={request.path}"
    )

# 主要入口：保存人工回答/取消；继续执行必须显式调用 ``forge resume``。
def respond_to_human_input_request(args: argparse.Namespace) -> str:
    """保存回答或取消状态；恢复执行仍由 ``forge resume`` 显式触发。"""

    request = persist_human_input_response(
        args.human_input_root,
        args.request_id,
        answer=getattr(args, "answer", "") or "",
        cancel=getattr(args, "cancel", False),
        note=getattr(args, "note", ""),
    )
    return (
        f"human input {request.status}: request_id={request.request_id} "
        f"path={request.path}"
    )

respond_to_human_input = respond_to_human_input_request

__all__ = ["approve_request", "respond_to_human_input"]
