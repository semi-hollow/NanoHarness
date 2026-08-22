import http.client
import json
import os
import urllib.error
import urllib.request
from typing import Any

from agent_forge.contracts import ToolSchema
from agent_forge.runtime.adapters.tool_call_normalizer import ToolCallNormalizer
from agent_forge.runtime.ports.model import ModelPort

from agent_forge.runtime.domain.conversation import AgentResponse, Message
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.adapters.model_config import LLMConfig


class LLMClient(ModelPort):
    """模型 Adapter 基类；显式实现 ``ModelPort`` 供 IDE 追踪装配关系。"""

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> AgentResponse:
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
        self.model = (
            model or os.getenv("AGENT_FORGE_MODEL") or os.getenv("OPENAI_MODEL", "")
        )
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

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> AgentResponse:
        if not self.is_configured():
            return self._invalid(
                "missing_config",
                "AGENT_FORGE_BASE_URL, AGENT_FORGE_API_KEY, and AGENT_FORGE_MODEL are required",
            )

        provider_request_payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                self._message_to_dict(provider_input_message)
                for provider_input_message in self._transport_messages(
                    messages,
                    tools,
                )
            ],
            "stream": False,
        }
        if self.thinking_mode != "enabled":
            provider_request_payload["temperature"] = self.temperature
        if self.thinking_mode != "auto":
            provider_request_payload["thinking"] = {"type": self.thinking_mode}
        if self.reasoning_effort:
            provider_request_payload["reasoning_effort"] = self.reasoning_effort
        if self.capabilities.native_tool_calling and tools:
            provider_request_payload["tools"] = [
                self._tool_to_openai_schema(tool) for tool in tools
            ]
        http_request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(provider_request_payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                # OpenCode Go 的 Cloudflare 边界会拒绝 urllib 默认 UA（1010）。
                # 显式产品标识同时让其他兼容 Provider 获得可诊断的请求来源。
                "User-Agent": "NanoHarness/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                http_request,
                timeout=self.timeout,
            ) as http_response:
                raw_response_body = http_response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw_response_body = exc.read().decode("utf-8", errors="replace")
            return self._invalid(
                self._classify_http_error(exc.code, raw_response_body),
                f"HTTP Error {exc.code}: {exc.reason}",
                raw_response_body[:1000],
            )
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            http.client.IncompleteRead,
        ) as exc:
            return self._invalid("request_failed", f"{type(exc).__name__}: {exc}")

        try:
            provider_response_payload = json.loads(raw_response_body)
        except json.JSONDecodeError as exc:
            return self._invalid(
                "invalid_json",
                str(exc),
                raw_response_body[:500],
            )

        return self.parse_response(provider_response_payload, tools=tools)

    def _transport_messages(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
    ) -> list[Message]:
        """原生工具不可用时，增加严格 JSON 协议而不伪造 provider tools。"""

        if self.capabilities.native_tool_calling or not tools:
            return messages
        visible_tool_catalog = [
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
        fallback_tool_protocol_instruction = "\n".join(
            [
                "This model transport has no native tool calling.",
                "To call a tool, return only one JSON object with this shape:",
                '{"name":"visible_tool_name","arguments":{"key":"value"}}',
                "Do not invent tool names or omit required arguments.",
                "Visible tools:",
                json.dumps(
                    visible_tool_catalog,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ]
        )
        return [
            *messages,
            Message(
                role="system",
                content=fallback_tool_protocol_instruction,
            ),
        ]

    def parse_response(
        self,
        provider_response_payload: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentResponse:
        """归一化 provider 响应，并对弱模型格式做受限修复。"""

        try:
            response_choices = provider_response_payload.get("choices")
            if not response_choices:
                return self._invalid("missing_choices", "response has no choices")
            assistant_message_payload = response_choices[0].get("message")
            if not isinstance(assistant_message_payload, dict):
                return self._invalid("missing_message", "first choice has no message")
            provider_content = assistant_message_payload.get("content")
            normalized_content = (
                str(provider_content) if provider_content is not None else None
            )
            provider_tool_calls = assistant_message_payload.get("tool_calls") or []
            if not isinstance(provider_tool_calls, list) or not all(
                isinstance(tool_call_payload, dict)
                for tool_call_payload in provider_tool_calls
            ):
                return self._invalid(
                    "invalid_tool_call",
                    "tool_calls must be a list of objects",
                    json.dumps(assistant_message_payload, ensure_ascii=False)[:1000],
                )
            legacy_function_call = assistant_message_payload.get("function_call")
            if legacy_function_call is not None and not isinstance(
                legacy_function_call,
                dict,
            ):
                return self._invalid(
                    "invalid_tool_call",
                    "function_call must be an object",
                    json.dumps(assistant_message_payload, ensure_ascii=False)[:1000],
                )
            normalized_tool_calls = self.tool_calls.normalize(
                raw_calls=provider_tool_calls,
                legacy_function_call=legacy_function_call,
                content=normalized_content,
                allowed_tool_names=self._allowed_tool_names(tools or []),
            )
            if normalized_tool_calls.error:
                return self._invalid(
                    "invalid_tool_call",
                    normalized_tool_calls.error,
                    json.dumps(assistant_message_payload, ensure_ascii=False)[:1000],
                    repair_prompt=normalized_tool_calls.repair_prompt,
                )
            normalized_content = normalized_tool_calls.content
            normalized_tool_call_objects = normalized_tool_calls.calls
            if normalized_content is None and not normalized_tool_call_objects:
                return self._invalid(
                    "empty_message", "message has neither content nor tool calls"
                )
            return AgentResponse(
                content=normalized_content,
                tool_calls=normalized_tool_call_objects,
                reasoning_content=assistant_message_payload.get("reasoning_content"),
                usage=provider_response_payload.get("usage"),
                response_id=provider_response_payload.get("id"),
                observed_model=(
                    str(provider_response_payload["model"])
                    if provider_response_payload.get("model")
                    else None
                ),
                normalization={
                    "tool_call_source": normalized_tool_calls.source,
                    "repairs": normalized_tool_calls.repairs,
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
        provider_message_payload: dict[str, Any] = {
            "role": message.role,
            "content": message.content,
        }

        if message.name and message.role != "tool":
            provider_message_payload["name"] = message.name
        if message.tool_call_id:
            provider_message_payload["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            provider_message_payload["tool_calls"] = message.tool_calls
        if message.reasoning_content:
            provider_message_payload["reasoning_content"] = message.reasoning_content
        return provider_message_payload

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

        normalized_error = raw.lower()
        context_markers = (
            "context_length_exceeded",
            "maximum context length",
            "context window",
            "too many tokens",
            "prompt is too long",
        )
        if any(marker in normalized_error for marker in context_markers):
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
            content=None,
            tool_calls=[],
            error={
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
