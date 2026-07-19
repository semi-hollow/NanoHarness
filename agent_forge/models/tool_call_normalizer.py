"""把弱模型常见的工具调用格式归一化到 Runtime 契约。"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from agent_forge.runtime.domain.conversation import ToolCall
from agent_forge.runtime.structured_output import StructuredOutputParser


@dataclass(frozen=True)
class ToolCallNormalizationResult:
    """归一化结果包含修复证据，失败时给出一次受限重试提示。"""

    calls: list[ToolCall] = field(default_factory=list)
    content: str | None = None
    repairs: list[str] = field(default_factory=list)
    source: str = "native"
    error: str = ""
    repair_prompt: str = ""


class ToolCallNormalizer:
    """只做确定性格式修复，不猜测工具名或缺失业务参数。"""

    # 主要入口：先处理原生 tool_calls，再尝试受约束的文本降级格式。
    def normalize(
        self,
        *,
        raw_calls: list[dict[str, Any]],
        legacy_function_call: dict[str, Any] | None,
        content: str | None,
        allowed_tool_names: set[str],
    ) -> ToolCallNormalizationResult:
        """把 provider wire format 转成类型化 ToolCall。"""

        rows = list(raw_calls)
        if legacy_function_call:
            rows.insert(
                0,
                {"id": "function_call", "function": legacy_function_call},
            )
        source = "native"
        repairs: list[str] = []
        if not rows and content:
            rows = self._extract_text_calls(content, allowed_tool_names)
            if rows:
                source = "text_fallback"
                repairs.append("text_tool_call_promoted")
        if not rows:
            return ToolCallNormalizationResult(content=content)

        calls: list[ToolCall] = []
        for index, raw in enumerate(rows):
            function = raw.get("function", raw)
            if not isinstance(function, dict):
                return self._failure(content, "tool call function must be an object")
            name = str(function.get("name") or function.get("tool") or "").strip()
            if not name:
                return self._failure(content, "tool call is missing a function name")
            arguments, argument_repair, error = self._arguments(
                function.get("arguments", {})
            )
            if error:
                return self._failure(content, f"{name} arguments: {error}")
            if argument_repair:
                repairs.append(f"{name}:{argument_repair}")
            calls.append(
                ToolCall(
                    id=str(raw.get("id") or f"call_{index}"),
                    name=name,
                    arguments=arguments,
                )
            )
        return ToolCallNormalizationResult(
            calls=calls,
            content=None if source == "text_fallback" else content,
            repairs=repairs,
            source=source,
        )

    def _extract_text_calls(
        self,
        content: str,
        allowed_tool_names: set[str],
    ) -> list[dict[str, Any]]:
        parsed = StructuredOutputParser({"type": "object"}).parse(content)
        if not parsed.ok or not isinstance(parsed.data, dict):
            return []
        data = parsed.data
        raw_calls = data.get("tool_calls")
        if isinstance(raw_calls, list) and all(
            isinstance(item, dict) for item in raw_calls
        ):
            candidates = list(raw_calls)
        elif data.get("name") or data.get("tool"):
            candidates = [data]
        else:
            return []
        names = []
        for candidate in candidates:
            function = candidate.get("function", candidate)
            if not isinstance(function, dict):
                return []
            names.append(str(function.get("name") or function.get("tool") or ""))
        if not names or any(name not in allowed_tool_names for name in names):
            return []
        return candidates

    def _arguments(
        self,
        value: object,
    ) -> tuple[dict[str, Any], str, str]:
        if value is None:
            return {}, "null_arguments_normalized", ""
        if isinstance(value, dict):
            return dict(value), "", ""
        if not isinstance(value, str):
            return {}, "", "must be an object or encoded object"

        parsed = StructuredOutputParser({"type": "object"}).parse(value or "{}")
        if parsed.ok and isinstance(parsed.data, dict):
            return dict(parsed.data), "json_arguments_extracted", ""
        try:
            literal = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            literal = None
        if isinstance(literal, dict):
            return dict(literal), "python_literal_arguments_repaired", ""
        return {}, "", parsed.error or "invalid encoded object"

    @staticmethod
    def _failure(
        content: str | None,
        error: str,
    ) -> ToolCallNormalizationResult:
        raw = content or ""
        prompt = "\n".join(
            [
                "Your previous tool call did not match the tool contract.",
                f"Error: {error}",
                "Return one valid tool call with JSON object arguments.",
                "Do not explain the repair and do not invent a tool name.",
                f"Previous content: {raw[:1000]}",
            ]
        )
        return ToolCallNormalizationResult(
            content=content,
            error=error,
            repair_prompt=prompt,
        )
