from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .failure_taxonomy import FailureDiagnosis, classify_case_result
from .types import BenchCaseResult


# PRIMARY ENTRYPOINT: classify one final case after all evaluation evidence exists.
def attach_failure_diagnosis(result: BenchCaseResult) -> BenchCaseResult:
    """Populate final diagnosis fields on one mutable benchmark case.

    ``run_swebench`` calls this only after local and optional official evaluation
    finish. It loads trace/usage evidence, delegates taxonomy classification,
    and updates the result consumed by case studies and aggregate reports.
    """

    diagnosis = diagnose_case_result(result)
    result.failure_class = diagnosis.failure_class
    result.diagnosis = diagnosis.summary
    result.diagnosis_evidence = diagnosis.evidence
    result.next_actions = diagnosis.next_actions
    return result


def diagnose_case_result(result: BenchCaseResult) -> FailureDiagnosis:
    """Classify a case result using status, final answer, trace, and usage."""

    usage = _read_json(_usage_json_path(result.usage_report_path))
    trace = _read_json(result.trace_path)
    return classify_case_result(result, usage, trace)


def _usage_json_path(usage_report_path: Path | None) -> Path | None:
    """Infer usage.json path from usage_report.md path."""

    if not usage_report_path:
        return None
    path = Path(usage_report_path)
    if path.name == "usage_report.md":
        return path.with_name("usage.json")
    if path.suffix == ".md":
        return path.with_suffix(".json")
    return path


def _read_json(path: Path | None) -> dict[str, Any]:
    """Read JSON artifacts without making report generation fragile."""

    if not path or not Path(path).exists():
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"read_error": str(exc), "path": str(path)}
