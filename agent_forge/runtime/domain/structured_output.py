"""LLM structured output 的确定性提取、schema 校验与 repair evidence。

系统角色：从可能带 Markdown/前后文本的模型输出提取第一个完整 JSON value，递归验证
typed schema，并在失败时返回明确 repair prompt；它不调用模型，也不执行业务计划。
输入：schema + raw text；输出：``StructuredOutputResult``。
相邻边界：Planner/Normalizer 决定是否进行一次模型 repair；Runtime 仍对解析后的 Domain
plan 做第二层业务/图校验。

折叠导航：1 result/config；2 parse/repair；3 JSON extraction；4 schema validation。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


# region 1. Result 与 parser config
@dataclass(frozen=True)
class StructuredOutputResult:

    ok: bool
    data: Any = None
    error: str = ""
    raw: str = ""
    repair_prompt: str = ""


class StructuredOutputParser:

    def __init__(self, schema: dict[str, Any], *, max_repair_attempts: int = 2) -> None:

        if not isinstance(schema, dict):
            raise TypeError("schema must be a dict")
        self.schema = schema
        self.max_repair_attempts = max(0, max_repair_attempts)
    # endregion 1. Result/config 结束

    # region 2. Parse 与 repair evidence
    # 主要入口：将模型文本协议解析为 final answer、tool calls 或明确错误。
    def parse(self, text: str) -> StructuredOutputResult:
        """把模型文本和 tool call 归一化为 AgentResponse。"""

        raw = text or ""
        candidate = self._extract_json_candidate(raw)
        if not candidate:
            return self._failure(raw, "no JSON object or array found")

        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            return self._failure(raw, f"invalid JSON: {exc}")

        error = self._validate_schema(data, self.schema, "$")
        if error:
            return self._failure(raw, error)
        return StructuredOutputResult(ok=True, data=data, raw=raw)

    def json_instructions(self) -> str:

        return (
            "Return only valid JSON. Do not wrap it in Markdown. "
            "The JSON must match this schema:\n"
            f"{json.dumps(self.schema, ensure_ascii=False, sort_keys=True)}"
        )

    def should_retry_repair(self, attempt_index: int) -> bool:

        return attempt_index < self.max_repair_attempts

    def _failure(self, raw: str, error: str) -> StructuredOutputResult:

        return StructuredOutputResult(
            ok=False,
            error=error,
            raw=raw,
            repair_prompt=self.build_repair_prompt(raw, error),
        )

    def build_repair_prompt(self, raw: str, error: str) -> str:

        return (
            "Repair the response into the required JSON contract.\n"
            "Your previous response did not match the required JSON contract.\n"
            f"Error: {error}\n"
            "Schema:\n"
            f"{json.dumps(self.schema, ensure_ascii=False, sort_keys=True)}\n"
            "Previous response:\n"
            f"{raw}\n"
            "Return only corrected JSON. No Markdown, no explanation."
        )
    # endregion 2. Parse/repair 结束

    # region 3. JSON extraction：fence 优先，再扫描平衡 object/array
    def _extract_json_candidate(self, text: str) -> str:

        fenced = self._extract_fenced_json(text)
        if fenced:
            return fenced
        return self._extract_balanced_json(text)

    def _extract_fenced_json(self, text: str) -> str:

        marker = "```"
        start = text.find(marker)
        # 逐个 fence 检查；只接受 json/空 header，其他代码块继续向后找。
        while start != -1:
            line_end = text.find("\n", start + len(marker))
            if line_end == -1:
                return ""
            fence_header = text[start + len(marker) : line_end].strip().lower()
            end = text.find(marker, line_end + 1)
            if end == -1:
                return ""
            body = text[line_end + 1 : end].strip()
            if fence_header in {"json", ""} and body:
                return body
            start = text.find(marker, end + len(marker))
        return ""

    def _extract_balanced_json(self, text: str) -> str:

        for index, char in enumerate(text):
            if char not in "{[":
                continue
            candidate = self._scan_balanced(text[index:])
            if candidate:
                return candidate
        return ""

    def _scan_balanced(self, text: str) -> str:

        stack: list[str] = []
        in_string = False
        escaped = False
        pairs = {"{": "}", "[": "]"}
        # stack 只在字符串之外解释括号，并正确跳过转义引号。
        for index, char in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char in pairs:
                stack.append(pairs[char])
                continue
            if char in "}]" and (not stack or char != stack.pop()):
                return ""
            if not stack:
                return text[: index + 1].strip()
        return ""
    # endregion 3. JSON extraction 结束

    # region 4. Recursive schema validation：只验证当前轻量 schema 支持的类型/字段
    def _validate_schema(self, data: Any, schema: dict[str, Any], path: str) -> str:

        expected_type = schema.get("type")
        if expected_type and not self._matches_type(data, expected_type):
            return f"{path} must be {expected_type}"

        if expected_type == "object" or isinstance(data, dict):
            if not isinstance(data, dict):
                return f"{path} must be object"
            required = schema.get("required", [])
            for name in required:
                if name not in data:
                    return f"{path}.{name} is required"
            properties = schema.get("properties", {})
            if not isinstance(properties, dict):
                return f"{path}.properties must be object in schema"
            for name, child_schema in properties.items():
                if name in data and isinstance(child_schema, dict):
                    error = self._validate_schema(data[name], child_schema, f"{path}.{name}")
                    if error:
                        return error

        if expected_type == "array" or isinstance(data, list):
            if not isinstance(data, list):
                return f"{path} must be array"
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(data):
                    error = self._validate_schema(item, item_schema, f"{path}[{index}]")
                    if error:
                        return error
        return ""

    def _matches_type(self, value: Any, expected_type: str | list[str]) -> bool:

        if isinstance(expected_type, list):
            return any(self._matches_type(value, item) for item in expected_type)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "null":
            return value is None
        return True
    # endregion 4. Schema validation 结束
