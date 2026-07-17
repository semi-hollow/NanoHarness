from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_forge.bench.domain.models import BenchCaseResult

RUN_REPORT_KEYS = {
    "resolved_ids": "official_resolved",
    "unresolved_ids": "official_eval_failed",
    "error_ids": "official_eval_error",
    "empty_patch_ids": "official_eval_skipped_empty_patch",
    "incomplete_ids": "official_eval_incomplete",
}


# 核心数据：official evaluator 对单题给出的明确 resolved/unresolved/error 事实。
@dataclass(frozen=True)
class OfficialCaseOutcome:
    """单题状态、resolved 布尔值、证据文件和解析来源。"""

    instance_id: str
    status: str
    resolved: bool | None
    report_path: Path | None = None
    source: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "status": self.status,
            "resolved": self.resolved,
            "report_path": str(self.report_path or ""),
            "source": self.source,
            "detail": self.detail,
        }


# 核心数据：一次 official run 的 per-case outcome、报告位置和解析警告。
@dataclass(frozen=True)
class OfficialResults:
    """按 instance id 索引的 official outcome 与 run 级解析警告。"""

    run_id: str
    report_path: Path | None
    outcomes: dict[str, OfficialCaseOutcome]
    warnings: list[str] = field(default_factory=list)

# 主要入口：解析 aggregate/per-case official JSON，并显式暴露缺失或冲突结果。
def parse_official_results(
    output_dir: str | Path,
    run_id: str,
    instance_ids: list[str],
) -> OfficialResults:
    """解析 official harness 输出并保留缺失或冲突警告。"""

    root = Path(output_dir)
    wanted = list(dict.fromkeys(instance_ids))
    warnings: list[str] = []
    report_path, run_report = _find_run_report(root, run_id, warnings)
    outcomes = {
        instance_id: _outcome_from_run_report(instance_id, run_report, report_path)
        for instance_id in wanted
    }

    for path in sorted((root / "logs" / "run_evaluation" / run_id).glob("**/report.json")):
        data = _read_json(path, warnings)
        if not isinstance(data, dict):
            continue
        for instance_id in wanted:
            item = data.get(instance_id)
            if not isinstance(item, dict) or not isinstance(item.get("resolved"), bool):
                continue
            status = "official_resolved" if item["resolved"] else "official_eval_failed"
            per_case = OfficialCaseOutcome(
                instance_id=instance_id,
                status=status,
                resolved=item["resolved"],
                report_path=path,
                source="per_case_report",
                detail="parsed explicit resolved boolean from official per-case report",
            )
            current = outcomes[instance_id]
            if current.status in {"official_resolved", "official_eval_failed"} and current.status != status:
                outcomes[instance_id] = OfficialCaseOutcome(
                    instance_id=instance_id,
                    status="official_eval_error",
                    resolved=None,
                    report_path=path,
                    source="conflicting_reports",
                    detail="conflicting aggregate and per-case official results",
                )
            elif current.status in {"official_eval_error", "official_eval_skipped_empty_patch"}:
                outcomes[instance_id] = OfficialCaseOutcome(
                    instance_id=instance_id,
                    status="official_eval_error",
                    resolved=None,
                    report_path=path,
                    source="conflicting_reports",
                    detail=f"conflicting {current.status} and explicit per-case result",
                )
            else:
                outcomes[instance_id] = per_case

    return OfficialResults(run_id=run_id, report_path=report_path, outcomes=outcomes, warnings=warnings)

# 运行时端口：把已解析 official outcome 写回对应 BenchCaseResult 证据层。
def apply_official_results(
    case_results: list[BenchCaseResult],
    parsed: OfficialResults,
    process_exit_code: int,
) -> None:

    for result in case_results:
        outcome = parsed.outcomes.get(result.instance_id)
        if outcome is None:
            status = "official_eval_error" if process_exit_code else "official_eval_incomplete"
            detail = "official parser did not return an outcome for this case"
            report_path = ""
        else:
            status = outcome.status
            detail = outcome.detail
            report_path = str(outcome.report_path or "")
            if status == "official_eval_incomplete" and process_exit_code != 0:
                status = "official_eval_error"
                detail = f"official evaluator exited with code {process_exit_code} before a case report was produced"

        result.official_evaluation_status = status
        result.official_evaluation_report_path = report_path
        result.official_evaluation_detail = detail
        result.evaluation_status = status


def _find_run_report(root: Path, run_id: str, warnings: list[str]) -> tuple[Path | None, dict[str, Any]]:
    for path in sorted(root.glob(f"*.{run_id}.json")):
        data = _read_json(path, warnings)
        if isinstance(data, dict) and any(key in data for key in RUN_REPORT_KEYS):
            return path, data
    return None, {}


def _outcome_from_run_report(
    instance_id: str,
    report: dict[str, Any],
    report_path: Path | None,
) -> OfficialCaseOutcome:
    memberships = [
        (key, status)
        for key, status in RUN_REPORT_KEYS.items()
        if instance_id in _string_set(report.get(key))
    ]
    if len(memberships) > 1:
        return OfficialCaseOutcome(
            instance_id=instance_id,
            status="official_eval_error",
            resolved=None,
            report_path=report_path,
            source="run_report",
            detail="conflicting outcome lists in official run report",
        )
    if not memberships:
        return OfficialCaseOutcome(
            instance_id=instance_id,
            status="official_eval_incomplete",
            resolved=None,
            report_path=report_path,
            source="run_report" if report_path else "missing_report",
            detail="no explicit per-case outcome was found",
        )
    key, status = memberships[0]
    return OfficialCaseOutcome(
        instance_id=instance_id,
        status=status,
        resolved=True if status == "official_resolved" else False if status == "official_eval_failed" else None,
        report_path=report_path,
        source="run_report",
        detail=f"instance id listed in {key}",
    )


def _read_json(path: Path, warnings: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"could not parse {path}: {exc}")
        return None


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value}
