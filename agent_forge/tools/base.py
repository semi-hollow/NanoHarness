"""模型可见 Tool 的最小框架契约。"""

from __future__ import annotations

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation


class Tool:
    """所有内置工具的基类，类似 Java 中的 SPI 接口。

    ``schema`` 面向模型声明可调用能力，``execute`` 面向 Runtime 返回统一 Observation；
    Tool 不负责决定自己在本轮是否可见，也不绕过统一授权链。
    """

    name: str = ""
    description: str = ""

    def schema(self) -> ToolSchema:
        """返回模型可见的名称、说明和参数契约。"""

        raise NotImplementedError

    def execute(self, arguments: ToolArguments) -> Observation:
        """执行一个已经由 Runtime 路由和授权的工具调用。"""

        raise NotImplementedError
