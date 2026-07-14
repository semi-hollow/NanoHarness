from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


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

    # 主要入口：下方定义承接该模块的核心调用。
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

    def _extract_json_candidate(self, text: str) -> str:

        fenced = self._extract_fenced_json(text)
        if fenced:
            return fenced
        return self._extract_balanced_json(text)

    def _extract_fenced_json(self, text: str) -> str:

        marker = "```"
        start = text.find(marker)
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
