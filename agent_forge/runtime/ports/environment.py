"""执行环境端口。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_forge.contracts import JsonObject


class EnvironmentProbeView(Protocol):
    """可写入 checkpoint 的环境快照。"""

    def to_dict(self) -> JsonObject:
        """返回 JSON-safe 环境证据。"""


class EnvironmentPort(Protocol):
    """Runtime 使用执行环境时真正需要的只读能力。"""

    def probe(self) -> EnvironmentProbeView:
        """返回当前执行边界证据。"""

    def render_boundary_summary(self) -> str:
        """返回模型可见的简短权限说明。"""


@runtime_checkable
class WorkspaceDiffPort(Protocol):
    """可选能力：读取当前工作区相对运行基线的真实改动。

    并非所有第三方执行环境都基于 Git，因此该能力不能放进稳定的
    ``EnvironmentPort``。Application 通过 ``isinstance`` 做结构化能力探测；不支持
    时继续使用普通工具路由，不会要求旧 Adapter 补一个无意义的空实现。
    """

    def diff(self) -> str:
        """返回 active workspace 相对基线的真实候选改动。"""
