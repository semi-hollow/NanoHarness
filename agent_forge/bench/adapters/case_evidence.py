from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_forge.bench.domain.models import BenchCaseResult


class JsonCaseEvidenceReader:

    def load_usage(self, result: BenchCaseResult) -> dict[str, Any]:
        return self._read_json(_usage_json_path(result.usage_report_path))

    def load_trace(self, result: BenchCaseResult) -> dict[str, Any]:
        return self._read_json(result.trace_path)

    @staticmethod
    def _read_json(path: Path | None) -> dict[str, Any]:
        if not path or not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"read_error": str(exc), "path": str(path)}
        return data if isinstance(data, dict) else {}


def usage_json_path(usage_report_path: Path | None) -> Path | None:
    if not usage_report_path:
        return None
    path = Path(usage_report_path)
    if path.name == "usage_report.md":
        return path.with_name("usage.json")
    if path.suffix == ".md":
        return path.with_suffix(".json")
    return path

_usage_json_path = usage_json_path
