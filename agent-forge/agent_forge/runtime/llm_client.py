import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from .message import Message
from .tool_call import ToolCall


@dataclass
class AgentResponse:
    content: Optional[str]
    tool_calls: list[ToolCall]
    error: Optional[dict[str, Any]] = None


class LLMClient:
    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:
        raise NotImplementedError


class OpenAICompatibleLLMClient(LLMClient):
    """Small standard-library client for OpenAI-compatible chat completions."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = 30,
    ):
        self.base_url = (base_url or os.getenv("AGENT_FORGE_BASE_URL") or os.getenv("OPENAI_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("AGENT_FORGE_API_KEY") or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("AGENT_FORGE_MODEL") or os.getenv("OPENAI_MODEL", "")
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "OpenAICompatibleLLMClient":
        return cls()

    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:
        if not self.is_configured():
            return self._invalid("missing_config", "AGENT_FORGE_BASE_URL, AGENT_FORGE_API_KEY, and AGENT_FORGE_MODEL are required")

        payload = {
            "model": self.model,
            "messages": [self._message_to_dict(m) for m in messages],
            "tools": [self._tool_to_openai_schema(t) for t in tools],
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return self._invalid("request_failed", str(exc))

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return self._invalid("invalid_json", str(exc), raw[:500])

        return self.parse_response(data)

    def parse_response(self, data: dict[str, Any]) -> AgentResponse:
        try:
            choices = data.get("choices")
            if not choices:
                return self._invalid("missing_choices", "response has no choices")
            message = choices[0].get("message")
            if not isinstance(message, dict):
                return self._invalid("missing_message", "first choice has no message")
            content = message.get("content")
            tool_calls = self._parse_tool_calls(message)
            if content is None and not tool_calls:
                return self._invalid("empty_message", "message has neither content nor tool calls")
            return AgentResponse(content, tool_calls)
        except Exception as exc:
            return self._invalid("parse_failed", str(exc))

    def _parse_tool_calls(self, message: dict[str, Any]) -> list[ToolCall]:
        calls = []
        raw_calls = message.get("tool_calls") or []
        if message.get("function_call"):
            raw_calls = [{"id": "function_call", "function": message["function_call"]}] + list(raw_calls)

        for index, raw in enumerate(raw_calls):
            fn = raw.get("function", raw)
            name = fn.get("name")
            if not name:
                raise ValueError("tool call missing function name")
            arguments = fn.get("arguments", {})
            if isinstance(arguments, str):
                arguments = json.loads(arguments or "{}")
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                raise ValueError("tool call arguments must be an object")
            calls.append(ToolCall(str(raw.get("id", f"call_{index}")), name, arguments))
        return calls

    def _message_to_dict(self, message: Message) -> dict[str, Any]:
        item = {"role": message.role, "content": message.content}
        if message.name:
            item["name"] = message.name
        if message.tool_call_id:
            item["tool_call_id"] = message.tool_call_id
        return item

    def _tool_to_openai_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        if schema.get("type") == "function":
            return schema
        properties = {}
        required = []
        for name, typ in schema.get("arguments", {}).items():
            required.append(name)
            properties[name] = {"type": self._json_type(typ)}
        return {
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def _json_type(self, typ: Any) -> str:
        if typ in {"int", "integer"}:
            return "integer"
        if typ in {"float", "number"}:
            return "number"
        if typ in {"bool", "boolean"}:
            return "boolean"
        if typ in {"list", "array"}:
            return "array"
        if typ in {"dict", "object"}:
            return "object"
        return "string"

    def _invalid(self, code: str, message: str, raw: str = "") -> AgentResponse:
        return AgentResponse(None, [], {"type": "invalid_response", "code": code, "message": message, "raw": raw})


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
                return AgentResponse(None, [ToolCall("4", "run_command", {"command": "python3.11 -m unittest discover examples/demo_repo/tests -t examples/demo_repo"})])
            return AgentResponse("已完成修复并验证测试通过。", [])
        return AgentResponse("multi mode by supervisor", [])
