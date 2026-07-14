"""Live fanout 的原子文件 artifact adapter。"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from ..domain.live import FanoutPlan, LiveFanoutSummary, LiveSubagentResult
from ..presentation.live_report import render_live_fanout_report


class FanoutFileRepository:
    """保存计划、恢复点、candidate patch 和最终 summary。"""

    def __init__(self, run_dir: str | Path) -> None:
        self.root = Path(run_dir).resolve() / "fanout"
        self.root.mkdir(parents=True, exist_ok=True)

    def write_plan(self, plan: FanoutPlan) -> str:
        path = self.root / "fanout_plan.json"
        _write_json_atomic(path, plan.to_dict())
        return str(path)

    def write_checkpoint(
        self,
        *,
        plan_digest: str,
        base_head: str,
        results: list[LiveSubagentResult],
        merged_task_ids: list[str],
        status: str,
    ) -> str:
        path = self.root / "fanout_checkpoint.json"
        _write_json_atomic(
            path,
            {
                "schema_version": 1,
                "status": status,
                "plan_digest": plan_digest,
                "base_head": base_head,
                "merged_task_ids": list(merged_task_ids),
                "results": [result.to_dict() for result in results],
                "updated_at": time.time(),
            },
        )
        return str(path)

    def write_integration_patch(self, patch: str) -> str:
        path = self.root / "integration.patch"
        path.write_text(patch, encoding="utf-8")
        return str(path)

    def write_summary(self, summary: LiveFanoutSummary) -> None:
        summary_path = self.root / "fanout_summary.json"
        report_path = self.root / "fanout_report.md"
        summary.summary_path = str(summary_path)
        summary.report_path = str(report_path)
        summary_path.write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report_path.write_text(
            render_live_fanout_report(summary),
            encoding="utf-8",
        )

    def load_resume(self, path: str) -> dict[str, Any]:
        resume_path = _resolve_resume_artifact(Path(path))
        data = json.loads(resume_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("fanout resume artifact must contain an object")
        return data

    def read_text(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")


def _resolve_resume_artifact(path: Path) -> Path:
    if path.is_file():
        return path
    roots = [path / "fanout", path]
    for filename in ("fanout_summary.json", "fanout_checkpoint.json"):
        for root in roots:
            candidate = root / filename
            if candidate.exists():
                return candidate
    raise FileNotFoundError(f"no fanout summary or checkpoint found under {path}")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
