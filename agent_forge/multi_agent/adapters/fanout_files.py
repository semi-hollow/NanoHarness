"""Multi-Agent canonical artifacts 的文件 Adapter。

系统角色：把计划、checkpoint、集成 Diff、summary/report 和 coordination JSONL 写入
一个 run 的 ``fanout/`` 目录。强一致 mutable state 使用 atomic JSON；coordination
使用单 Runtime 顺序下的 append+flush JSONL。

折叠导航：1 canonical 写入；2 resume/read；3 coordination；4 路径解析。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from agent_forge.infrastructure.atomic_json import atomic_write_json

from ..domain.live import FanoutCheckpoint, FanoutPlan, LiveFanoutSummary
from ..ports import FanoutArtifactPort
from ..presentation.live_report import render_live_fanout_report


class FanoutFileRepository(FanoutArtifactPort):
    """保存计划、恢复点、合并 diff 和最终 summary。"""

    # region 1. Canonical 写入：plan/checkpoint/diff/summary 各自只有一个稳定路径
    def __init__(self, run_dir: str | Path) -> None:
        self.root = Path(run_dir).resolve() / "fanout"
        self.root.mkdir(parents=True, exist_ok=True)
        self._coordination_lock = threading.Lock()

    def write_plan(self, plan: FanoutPlan) -> str:
        path = self.root / "fanout_plan.json"
        atomic_write_json(path, plan.to_dict())
        return str(path)

    def write_checkpoint(self, checkpoint: FanoutCheckpoint) -> str:
        path = self.root / "fanout_checkpoint.json"
        atomic_write_json(
            path,
            {
                "schema_version": 2,
                "status": checkpoint.status,
                "plan_digest": checkpoint.plan_digest,
                "initial_plan_identity": checkpoint.initial_plan_identity,
                "effective_plan": (
                    checkpoint.effective_plan.to_dict()
                    if checkpoint.effective_plan is not None
                    else None
                ),
                "effective_plan_digest": checkpoint.effective_plan_digest,
                "replan_round": checkpoint.replan_round,
                "base_head": checkpoint.base_head,
                "merged_task_ids": list(checkpoint.merged_task_ids),
                "results": [result.to_dict() for result in checkpoint.results],
                "attempt_results": [
                    result.to_dict() for result in checkpoint.attempt_results
                ],
                "updated_at": time.time(),
            },
        )
        return str(path)

    def write_integrated_diff(self, diff_text: str) -> str:
        """保存所有成功 worker 已合入集成 workspace 的最终 diff。"""

        path = self.root / "integrated_changes.diff"
        path.write_text(diff_text, encoding="utf-8")
        return str(path)

    def write_summary(self, summary: LiveFanoutSummary) -> None:
        summary_path = self.root / "fanout_summary.json"
        report_path = self.root / "fanout_report.md"
        summary.summary_path = str(summary_path)
        summary.report_path = str(report_path)
        atomic_write_json(summary_path, summary.to_dict())
        report_path.write_text(
            render_live_fanout_report(summary),
            encoding="utf-8",
        )
    # endregion 1. Canonical 写入结束

    # region 2. Resume 与只读 artifact：返回未信任 mapping，由 Application 继续校验
    def load_resume(self, path: str) -> dict[str, Any]:
        resume_path = _resolve_resume_artifact(Path(path))
        data = json.loads(resume_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("fanout resume artifact must contain an object")
        return data

    def read_text(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")
    # endregion 2. Resume 与只读 artifact 结束

    # region 3. Coordination：每个事实一行，持久化成功才允许 Runtime 提交内存状态
    def append_coordination(self, record: dict[str, Any]) -> str:
        """每条事实一行并立即 flush；Runtime lock 维护跨字段提交顺序。"""

        path = self.root / "coordination.jsonl"
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._coordination_lock, path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
            stream.flush()
        return str(path)
    # endregion 3. Coordination 结束


# region 4. 路径解析：兼容 run root 或直接 artifact 文件，不承载 schema fallback
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
# endregion 4. 路径解析结束
