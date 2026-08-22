"""把弱模型常见的工具调用格式归一化到 Runtime 契约。"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from agent_forge.runtime.domain.conversation import ToolCall
from agent_forge.runtime.domain.structured_output import StructuredOutputParser


@dataclass(frozen=True, kw_only=True)
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
        """把原生、legacy 或受约束文本格式转换为 Runtime 的类型化 ToolCall。

        参数只做可确定的对象解析和字面量修复，并记录修复来源；文本格式仅在所有工具名
        均属于允许集合时提升为 ToolCall。畸形调用返回纠错提示，但不猜测工具名或业务参数。
        """

        tool_call_rows = list(raw_calls)
        if legacy_function_call:
            tool_call_rows.insert(
                0,
                {"id": "function_call", "function": legacy_function_call},
            )
        tool_call_source = "native"
        normalization_repairs: list[str] = []
        if not tool_call_rows and content:
            tool_call_rows = self._extract_text_calls(
                content,
                allowed_tool_names,
            )
            if tool_call_rows:
                tool_call_source = "text_fallback"
                normalization_repairs.append("text_tool_call_promoted")
        if not tool_call_rows:
            return ToolCallNormalizationResult(content=content)

        normalized_tool_calls: list[ToolCall] = []
        for index, provider_tool_call in enumerate(tool_call_rows):
            function_payload = provider_tool_call.get(
                "function",
                provider_tool_call,
            )
            if not isinstance(function_payload, dict):
                return self._failure(content, "tool call function must be an object")
            tool_name = str(
                function_payload.get("name") or function_payload.get("tool") or ""
            ).strip()
            if not tool_name:
                return self._failure(content, "tool call is missing a function name")
            normalized_arguments, argument_repair, argument_error = self._arguments(
                function_payload.get("arguments", {})
            )
            if argument_error:
                return self._failure(
                    content,
                    f"{tool_name} arguments: {argument_error}",
                )
            if argument_repair:
                normalization_repairs.append(f"{tool_name}:{argument_repair}")
            normalized_tool_calls.append(
                ToolCall(
                    id=str(provider_tool_call.get("id") or f"call_{index}"),
                    name=tool_name,
                    arguments=normalized_arguments,
                )
            )
        return ToolCallNormalizationResult(
            calls=normalized_tool_calls,
            content=None if tool_call_source == "text_fallback" else content,
            repairs=normalization_repairs,
            source=tool_call_source,
        )

    def _extract_text_calls(
        self,
        content: str,
        allowed_tool_names: set[str],
    ) -> list[dict[str, Any]]:
        parsed_tool_call_payload = StructuredOutputParser({"type": "object"}).parse(
            content
        )
        if not parsed_tool_call_payload.ok or not isinstance(
            parsed_tool_call_payload.data,
            dict,
        ):
            return []
        structured_call_data = parsed_tool_call_payload.data
        structured_tool_calls = structured_call_data.get("tool_calls")
        if isinstance(structured_tool_calls, list) and all(
            isinstance(tool_call_payload, dict)
            for tool_call_payload in structured_tool_calls
        ):
            candidate_call_payloads = list(structured_tool_calls)
        elif structured_call_data.get("name") or structured_call_data.get("tool"):
            candidate_call_payloads = [structured_call_data]
        else:
            return []
        candidate_tool_names = []
        for candidate_call_payload in candidate_call_payloads:
            function_payload = candidate_call_payload.get(
                "function",
                candidate_call_payload,
            )
            if not isinstance(function_payload, dict):
                return []
            candidate_tool_names.append(
                str(function_payload.get("name") or function_payload.get("tool") or "")
            )
        if not candidate_tool_names or any(
            tool_name not in allowed_tool_names for tool_name in candidate_tool_names
        ):
            return []
        return candidate_call_payloads

    def _arguments(
        self,
        encoded_arguments: object,
    ) -> tuple[dict[str, Any], str, str]:
        if encoded_arguments is None:
            return {}, "null_arguments_normalized", ""
        if isinstance(encoded_arguments, dict):
            return dict(encoded_arguments), "", ""
        if not isinstance(encoded_arguments, str):
            return {}, "", "must be an object or encoded object"

        parsed_arguments = StructuredOutputParser({"type": "object"}).parse(
            encoded_arguments or "{}"
        )
        if parsed_arguments.ok and isinstance(parsed_arguments.data, dict):
            return dict(parsed_arguments.data), "json_arguments_extracted", ""
        try:
            literal_arguments = ast.literal_eval(encoded_arguments)
        except (SyntaxError, ValueError):
            literal_arguments = None
        if isinstance(literal_arguments, dict):
            return (
                dict(literal_arguments),
                "python_literal_arguments_repaired",
                "",
            )
        return {}, "", parsed_arguments.error or "invalid encoded object"

    @staticmethod
    def _failure(
        content: str | None,
        error: str,
    ) -> ToolCallNormalizationResult:
        failed_content = content or ""
        repair_prompt = "\n".join(
            [
                "Your previous tool call did not match the tool contract.",
                f"Error: {error}",
                "Return one valid tool call with JSON object arguments.",
                "Do not explain the repair and do not invent a tool name.",
                f"Previous content: {failed_content[:1000]}",
            ]
        )
        return ToolCallNormalizationResult(
            content=content,
            error=error,
            repair_prompt=repair_prompt,
        )
