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
            plan = [
                ToolCall("1", "read_file", {"path": "examples/demo_repo/src/calculator.py"}),
                ToolCall("2", "read_file", {"path": "examples/demo_repo/tests/test_calculator.py"}),
                ToolCall("3", "apply_patch", {"path": "examples/demo_repo/src/calculator.py", "old": "return a - b", "new": "return a + b"}),
                ToolCall("4", "run_command", {"command": "python -m unittest discover examples/demo_repo/tests"}),
            ]
            if i < len(plan):
                return AgentResponse(None, [plan[i]])
            return AgentResponse("已完成修复并验证测试通过。", [])
        return AgentResponse("multi mode by supervisor", [])
