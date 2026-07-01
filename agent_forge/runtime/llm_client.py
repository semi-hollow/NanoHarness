import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from .message import Message
from .llm_config import LLMConfig
from .structured_output import StructuredOutputParser
from .tool_call import ToolCall


@dataclass
class AgentResponse:
    """Normalized LLM response: final text, tool calls, or structured error."""

    # Final natural-language answer. None when the model chose tools only.
    content: Optional[str]

    # Normalized tool calls independent of provider wire format.
    tool_calls: list[ToolCall]

    # Structured provider/parse failure. AgentLoop treats it as data, not crash.
    error: Optional[dict[str, Any]] = None

    # Provider-specific reasoning text. DeepSeek thinking-mode models require
    # this field to be passed back in later assistant messages, so the runtime
    # preserves it without exposing it as user-facing final answer text.
    reasoning_content: Optional[str] = None

    # Raw provider token usage, when the API returns it. DeepSeek/OpenAI put
    # cache, prompt, completion, and total token accounting here.
    usage: Optional[dict[str, Any]] = None

    # Provider response id. This lets usage reports point from one logical
    # AgentLoop step back to the exact upstream model response.
    response_id: Optional[str] = None


class LLMClient:
    """Interface used by AgentLoop regardless of the backing provider."""

    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:
        """Return either final content or tool calls for the next loop step."""

        raise NotImplementedError


class OpenAICompatibleLLMClient(LLMClient):
    """Small standard-library client for OpenAI-compatible chat completions.

    The project avoids a provider SDK so the protocol is visible: build request,
    parse choices/message/tool_calls, normalize into AgentResponse.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = 30,
    ):
        """Read OpenAI-compatible connection settings from args or env."""

        self.base_url = (
            base_url or os.getenv("AGENT_FORGE_BASE_URL") or os.getenv("OPENAI_BASE_URL", "")
        ).rstrip("/")
        self.api_key = api_key or os.getenv("AGENT_FORGE_API_KEY") or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("AGENT_FORGE_MODEL") or os.getenv("OPENAI_MODEL", "")
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "OpenAICompatibleLLMClient":
        """Build a client from AGENT_FORGE_* or OPENAI_* environment variables."""

        return cls()

    @classmethod
    def from_config(cls, config: LLMConfig) -> "OpenAICompatibleLLMClient":
        """Build a client from resolved CLI/profile/env configuration."""

        return cls(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            timeout=config.timeout,
        )

    def is_configured(self) -> bool:
        """Return whether enough connection fields exist for a real API call."""

        return bool(self.base_url and self.api_key and self.model)

    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:
        """Send a chat completion request and normalize provider output."""

        if not self.is_configured():
            return self._invalid(
                "missing_config",
                "AGENT_FORGE_BASE_URL, AGENT_FORGE_API_KEY, and AGENT_FORGE_MODEL are required",
            )

        payload = {
            "model": self.model,
            "messages": [self._message_to_dict(m) for m in messages],
            # Local tool schemas are converted to OpenAI function-tool shape so
            # Ollama/company gateways/OpenAI-compatible services share one path.
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
        except urllib.error.HTTPError as exc:
            # HTTPError is also a URLError, but it carries a response body.
            # Reading it makes provider-specific 400 messages visible in trace.
            raw = exc.read().decode("utf-8", errors="replace")
            return self._invalid("request_failed", f"HTTP Error {exc.code}: {exc.reason}", raw[:1000])
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return self._invalid("request_failed", str(exc))

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return self._invalid("invalid_json", str(exc), raw[:500])

        return self.parse_response(data)

    def parse_response(self, data: dict[str, Any]) -> AgentResponse:
        """Parse OpenAI-compatible response JSON into AgentResponse.

        Provider responses are treated defensively. Missing choices/message or
        empty output become structured errors so ModelGateway can retry/fallback.
        """

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
            return AgentResponse(
                content,
                tool_calls,
                reasoning_content=message.get("reasoning_content"),
                usage=data.get("usage"),
                response_id=data.get("id"),
            )
        except Exception as exc:
            return self._invalid("parse_failed", str(exc))

    def _parse_tool_calls(self, message: dict[str, Any]) -> list[ToolCall]:
        """Handle both modern `tool_calls` and legacy `function_call` shapes."""

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
                # Providers send function arguments as JSON strings; local tools
                # expect dicts so registry validation can inspect them. Use the
                # structured-output parser here so malformed/fenced JSON is
                # treated the same way as other LLM structured outputs.
                parser = StructuredOutputParser({"type": "object"})
                result = parser.parse(arguments or "{}")
                if not result.ok:
                    raise ValueError(
                        "tool call arguments are not valid JSON object: "
                        f"{result.error}; repair_prompt={result.repair_prompt}"
                    )
                arguments = result.data
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                raise ValueError("tool call arguments must be an object")
            calls.append(ToolCall(str(raw.get("id", f"call_{index}")), name, arguments))
        return calls

    def _message_to_dict(self, message: Message) -> dict[str, Any]:
        """Convert internal Message into chat-completions message format."""

        item = {"role": message.role, "content": message.content}
        # OpenAI-compatible providers differ on whether tool-role messages may
        # include `name`. `tool_call_id` is the modern linkage; omitting `name`
        # keeps DeepSeek and stricter gateways happy.
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
        """Convert local tool schema into OpenAI function-tool schema."""

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
        """Map local shorthand argument types to JSON Schema primitive types."""

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
        """Return provider/parse failures as data instead of throwing upward."""

        return AgentResponse(
            None,
            [],
            {"type": "invalid_response", "code": code, "message": message, "raw": raw},
        )
