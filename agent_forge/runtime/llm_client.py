import http.client
import json
import os
import urllib.error
import urllib.request
from typing import Any

from agent_forge.models.tool_call_normalizer import ToolCallNormalizer

from .domain.conversation import AgentResponse, Message
from .domain.model import ModelCapabilities
from .llm_config import LLMConfig


class LLMClient:

    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:

        raise NotImplementedError


class OpenAICompatibleLLMClient(LLMClient):

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = 30,
        temperature: float = 0.0,
        thinking_mode: str = "auto",
        reasoning_effort: str | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> None:

        resolved_base_url = (
            base_url
            or os.getenv("AGENT_FORGE_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or ""
        )
        self.base_url = resolved_base_url.rstrip("/")
        self.api_key = (
            api_key
            or os.getenv("AGENT_FORGE_API_KEY")
            or os.getenv("OPENAI_API_KEY", "")
        )
        self.model = model or os.getenv("AGENT_FORGE_MODEL") or os.getenv("OPENAI_MODEL", "")
        self.timeout = timeout
        self.temperature = temperature
        self.thinking_mode = thinking_mode
        self.reasoning_effort = reasoning_effort
        self.capabilities = capabilities or ModelCapabilities()
        self.tool_calls = ToolCallNormalizer()

    @classmethod
    def from_env(cls) -> "OpenAICompatibleLLMClient":

        return cls()

    @classmethod
    def from_config(cls, config: LLMConfig) -> "OpenAICompatibleLLMClient":

        return cls(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            timeout=config.timeout,
            temperature=config.temperature,
            thinking_mode=config.thinking_mode,
            reasoning_effort=config.reasoning_effort,
            capabilities=config.capabilities,
        )

    def is_configured(self) -> bool:

        return bool(self.base_url and self.api_key and self.model)

    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:

        if not self.is_configured():
            return self._invalid(
                "missing_config",
                "AGENT_FORGE_BASE_URL, AGENT_FORGE_API_KEY, and AGENT_FORGE_MODEL are required",
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                self._message_to_dict(message)
                for message in self._transport_messages(messages, tools)
            ],
            "stream": False,
        }
        if self.thinking_mode != "enabled":
            payload["temperature"] = self.temperature
        if self.thinking_mode != "auto":
            payload["thinking"] = {"type": self.thinking_mode}
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.capabilities.native_tool_calling and tools:
            payload["tools"] = [self._tool_to_openai_schema(tool) for tool in tools]
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
        except urllib.error.HTTPError as exc:

            raw = exc.read().decode("utf-8", errors="replace")
            return self._invalid(
                self._classify_http_error(exc.code, raw),
                f"HTTP Error {exc.code}: {exc.reason}",
                raw[:1000],
            )
        except (urllib.error.URLError, TimeoutError, OSError, http.client.IncompleteRead) as exc:
            return self._invalid("request_failed", f"{type(exc).__name__}: {exc}")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return self._invalid("invalid_json", str(exc), raw[:500])

        return self.parse_response(data, tools=tools)

    def _transport_messages(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
    ) -> list[Message]:
        """原生工具不可用时，增加严格 JSON 协议而不伪造 provider tools。"""

        if self.capabilities.native_tool_calling or not tools:
            return messages
        catalog = [
            {
                "name": _tool_definition(tool).get("name", ""),
                "description": _tool_definition(tool).get("description", ""),
                "arguments": _tool_definition(tool).get(
                    "arguments",
                    _tool_definition(tool).get("parameters", {}),
                ),
            }
            for tool in tools
        ]
        instruction = "\n".join(
            [
                "This model transport has no native tool calling.",
                "To call a tool, return only one JSON object with this shape:",
                '{"name":"visible_tool_name","arguments":{"key":"value"}}',
                "Do not invent tool names or omit required arguments.",
                "Visible tools:",
                json.dumps(catalog, ensure_ascii=False, separators=(",", ":")),
            ]
        )
        return [*messages, Message("system", instruction)]

    def parse_response(
        self,
        data: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentResponse:
        """归一化 provider 响应，并对弱模型格式做受限修复。"""

        try:
            choices = data.get("choices")
            if not choices:
                return self._invalid("missing_choices", "response has no choices")
            message = choices[0].get("message")
            if not isinstance(message, dict):
                return self._invalid("missing_message", "first choice has no message")
            raw_content = message.get("content")
            content = str(raw_content) if raw_content is not None else None
            raw_calls = message.get("tool_calls") or []
            if not isinstance(raw_calls, list) or not all(
                isinstance(item, dict) for item in raw_calls
            ):
                return self._invalid(
                    "invalid_tool_call",
                    "tool_calls must be a list of objects",
                    json.dumps(message, ensure_ascii=False)[:1000],
                )
            legacy = message.get("function_call")
            if legacy is not None and not isinstance(legacy, dict):
                return self._invalid(
                    "invalid_tool_call",
                    "function_call must be an object",
                    json.dumps(message, ensure_ascii=False)[:1000],
                )
            normalized = self.tool_calls.normalize(
                raw_calls=raw_calls,
                legacy_function_call=legacy,
                content=content,
                allowed_tool_names=self._allowed_tool_names(tools or []),
            )
            if normalized.error:
                return self._invalid(
                    "invalid_tool_call",
                    normalized.error,
                    json.dumps(message, ensure_ascii=False)[:1000],
                    repair_prompt=normalized.repair_prompt,
                )
            content = normalized.content
            tool_calls = normalized.calls
            if content is None and not tool_calls:
                return self._invalid("empty_message", "message has neither content nor tool calls")
            return AgentResponse(
                content,
                tool_calls,
                reasoning_content=message.get("reasoning_content"),
                usage=data.get("usage"),
                response_id=data.get("id"),
                normalization={
                    "tool_call_source": normalized.source,
                    "repairs": normalized.repairs,
                },
            )
        except Exception as exc:
            return self._invalid("parse_failed", str(exc))

    @staticmethod
    def _allowed_tool_names(tools: list[dict[str, Any]]) -> set[str]:
        names: set[str] = set()
        for tool in tools:
            function = tool.get("function") if tool.get("type") == "function" else tool
            if isinstance(function, dict) and function.get("name"):
                names.add(str(function["name"]))
        return names

    def _message_to_dict(self, message: Message) -> dict[str, Any]:

        item: dict[str, Any] = {"role": message.role, "content": message.content}

        if message.name and message.role != "tool":
            item["name"] = message.name
        if message.tool_call_id:
            item["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            item["tool_calls"] = message.tool_calls
        if message.reasoning_content:
            item["reasoning_content"] = message.reasoning_content
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

    @staticmethod
    def _classify_http_error(status: int, raw: str) -> str:
        """先识别需要改变请求的错误，再区分可重试 transport 状态。"""

        lowered = raw.lower()
        context_markers = (
            "context_length_exceeded",
            "maximum context length",
            "context window",
            "too many tokens",
            "prompt is too long",
        )
        if any(marker in lowered for marker in context_markers):
            return "context_length_exceeded"
        if status == 408:
            return "request_timeout"
        if status == 429:
            return "rate_limited"
        if status >= 500:
            return "server_error"
        return "request_failed"

    def _invalid(
        self,
        code: str,
        message: str,
        raw: str = "",
        **details: Any,
    ) -> AgentResponse:

        return AgentResponse(
            None,
            [],
            {
                "type": "invalid_response",
                "code": code,
                "message": message,
                "raw": raw,
                **details,
            },
        )


def _tool_definition(tool: dict[str, Any]) -> dict[str, Any]:
    function = tool.get("function") if tool.get("type") == "function" else tool
    return function if isinstance(function, dict) else {}
