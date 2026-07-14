"""执行环境端口。"""

from __future__ import annotations

from typing import Protocol

from agent_forge.contracts import JsonObject


class EnvironmentProbeView(Protocol):
    """可写入 checkpoint 的环境快照。"""

    def to_dict(self) -> JsonObject:
        """返回 JSON-safe 环境证据。"""


class EnvironmentPort(Protocol):
    """Runtime 使用执行环境时真正需要的只读能力。"""

    def probe(self) -> EnvironmentProbeView:
        """返回当前执行边界证据。"""

    def describe(self) -> str:
        """返回模型可见的简短权限说明。"""
