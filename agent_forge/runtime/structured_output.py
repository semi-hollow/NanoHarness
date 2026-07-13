from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StructuredOutputResult:
    """Result of parsing and validating an LLM structured-output response.

    Field meanings:
        ok: True only when JSON was found and passed schema validation.
        data: Parsed JSON object/list when ok is True.
        error: Human-readable failure reason for trace and repair prompts.
        raw: Original model text. Stored so failures are debuggable.
        repair_prompt: Prompt fragment for one follow-up model repair attempt.
    """

    ok: bool
    data: Any = None
    error: str = ""
    raw: str = ""
    repair_prompt: str = ""


class StructuredOutputParser:
    """Parse, validate, and repair-prompt LLM JSON outputs.

    Why this class exists:
        Production agents often need JSON for plans, tool arguments, routing
        decisions, or evaluator scores. Prompting "return JSON" is not enough:
        models may wrap JSON in Markdown, omit required fields, or use the wrong
        type. This parser gives the runtime a deterministic layer before data is
        trusted or passed to tools.

    What it deliberately does:
        - accepts raw JSON, fenced ```json blocks, or text containing one JSON
          object/array;
        - validates the subset of JSON Schema this project needs;
        - returns a repair prompt instead of silently guessing.
    """

    def __init__(self, schema: dict[str, Any], *, max_repair_attempts: int = 2) -> None:
        """Store a JSON Schema-like contract and retry budget.

        max_repair_attempts belongs here instead of being hidden in prompts so
        callers can make the retry budget visible in trace and avoid endless
        "please fix your JSON" loops.
        """

        if not isinstance(schema, dict):
            raise TypeError("schema must be a dict")
        self.schema = schema
        self.max_repair_attempts = max(0, max_repair_attempts)

    # PRIMARY ENTRYPOINT: validate one model response against the requested schema.
    def parse(self, text: str) -> StructuredOutputResult:
        """Parse model text into validated JSON or a deterministic error."""

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
        """Return stable prompt instructions for the model call.

        Keeping this prefix stable improves provider-side prompt/cache reuse and
        prevents each caller from inventing slightly different JSON rules.
        """

        return (
            "Return only valid JSON. Do not wrap it in Markdown. "
            "The JSON must match this schema:\n"
            f"{json.dumps(self.schema, ensure_ascii=False, sort_keys=True)}"
        )

    def should_retry_repair(self, attempt_index: int) -> bool:
        """Return whether another repair call is allowed.

        attempt_index is zero-based for the first repair attempt. The method is
        tiny, but naming it makes the stop condition explicit when an evaluator
        or planner uses structured output.
        """

        return attempt_index < self.max_repair_attempts

    def _failure(self, raw: str, error: str) -> StructuredOutputResult:
        """Build a parse failure plus a repair prompt for one controlled retry."""

        return StructuredOutputResult(
            ok=False,
            error=error,
            raw=raw,
            repair_prompt=self.build_repair_prompt(raw, error),
        )

    def build_repair_prompt(self, raw: str, error: str) -> str:
        """Tell the model exactly what failed and ask for JSON only."""

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
        """Extract JSON from raw text without using fragile regex-only parsing."""

        fenced = self._extract_fenced_json(text)
        if fenced:
            return fenced
        return self._extract_balanced_json(text)

    def _extract_fenced_json(self, text: str) -> str:
        """Prefer fenced JSON blocks when the model includes Markdown."""

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
        """Return the first balanced object/array candidate in the text."""

        for index, char in enumerate(text):
            if char not in "{[":
                continue
            candidate = self._scan_balanced(text[index:])
            if candidate:
                return candidate
        return ""

    def _scan_balanced(self, text: str) -> str:
        """Scan one balanced JSON object/array while respecting strings."""

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
        """Validate the JSON Schema subset used by prompts and tool repair."""

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
        """Map JSON Schema primitive names to Python runtime checks."""

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
