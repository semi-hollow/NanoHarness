from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json_if_exists(path: str | Path | None) -> dict[str, Any]:

    if not path:
        return {}
    artifact = Path(path)
    if not artifact.exists():
        return {}
    try:
        data = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class JsonCaseEvidenceReader:

    def load_usage(self, case: dict[str, Any], run_dir: Path) -> dict[str, Any]:
        candidates: list[Path] = []
        report_value = str(case.get("usage_report_path") or "").strip()
        if report_value:
            report = Path(report_value)
            candidates.append(
                report.with_name("usage.json")
                if report.name == "usage_report.md"
                else report.with_suffix(".json")
            )
        instance_id = _safe_id(str(case.get("instance_id") or ""))
        candidates.append(run_dir / "cases" / instance_id / "usage.json")
        return _first_json_object(candidates)

    def load_environment(self, case: dict[str, Any], run_dir: Path) -> dict[str, Any]:
        candidates: list[Path] = []
        for key in ("patch_path", "trace_path"):
            value = str(case.get(key) or "").strip()
            if not value:
                continue
            artifact = Path(value)
            if not artifact.is_absolute():
                artifact = run_dir / artifact
            candidates.append(artifact.parent / "execution_environment.json")
        instance_id = _safe_id(str(case.get("instance_id") or ""))
        candidates.append(run_dir / "cases" / instance_id / "execution_environment.json")
        return _first_json_object(candidates)


def read_json_object(path: Path) -> dict[str, Any]:

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read JSON artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON artifact: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"JSON artifact must contain an object: {path}")
    return data


def write_json_object(path: Path, payload: dict[str, Any]) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _first_json_object(candidates: list[Path]) -> dict[str, Any]:
    for path in candidates:
        data = load_json_if_exists(path)
        if data:
            return data
    return {}


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
