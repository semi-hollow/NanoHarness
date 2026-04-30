from dataclasses import dataclass
from typing import Optional

from .message import Message
from .tool_call import ToolCall


@dataclass
class AgentResponse:
    content: Optional[str]
    tool_calls: list[ToolCall]


class LLMClient:
    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:
        raise NotImplementedError


class MockLLMClient(LLMClient):
    def __init__(self, mode: str = "single"):
        self.mode = mode

    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:
        tool_obs = [m for m in messages if m.role == "tool"]
        i = len(tool_obs)
        if self.mode == "single":
            if i == 0:
                return AgentResponse(None, [ToolCall("1", "read_file", {"path": "examples/demo_repo/src/calculator.py"})])
            if i == 1:
                return AgentResponse(None, [ToolCall("2", "read_file", {"path": "examples/demo_repo/tests/test_calculator.py"})])
            if i == 2:
                # first patch intentionally fails
                return AgentResponse(None, [ToolCall("3", "apply_patch", {"path": "examples/demo_repo/src/calculator.py", "old": "return a * b", "new": "return a + b"})])
            if i == 3 and "old text not found" in tool_obs[-1].content:
                return AgentResponse(None, [ToolCall("3b", "apply_patch", {"path": "examples/demo_repo/src/calculator.py", "old": "return a - b", "new": "return a + b"})])
            if i in {3, 4}:
                return AgentResponse(None, [ToolCall("4", "run_command", {"command": "python -m unittest discover examples/demo_repo/tests -t examples/demo_repo"})])
            return AgentResponse("已完成修复并验证测试通过。", [])
        return AgentResponse("multi mode by supervisor", [])
