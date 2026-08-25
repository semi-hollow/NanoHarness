"""Multi-Agent canonical artifacts 的文件 Adapter。

系统角色：把计划、checkpoint、集成 Diff、summary/report 和 coordination JSONL 写入
一个 run 的 ``fanout/`` 目录。强一致 mutable state 使用 atomic JSON；coordination
使用单 Runtime 顺序下的 append+flush JSONL。

折叠导航：1 canonical 写入；2 resume/read；3 coordination；4 路径解析。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from agent_forge.infrastructure.atomic_json import atomic_write_json

from ..domain.live import FanoutCheckpoint, FanoutPlan, FanoutSummary
from ..ports import FanoutArtifactPort
from ..presentation.live_report import render_fanout_report


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
                "schema_version": checkpoint.schema_version,
                "status": checkpoint.status,
                "plan_digest": checkpoint.plan_digest,
                "base_head": checkpoint.base_head,
                "merged_task_ids": list(checkpoint.merged_task_ids),
                "task_results": [
                    result.to_dict() for result in checkpoint.task_results
                ],
                "attempt_results": [
                    result.to_dict() for result in checkpoint.attempt_results
                ],
                "launch_waves": [
                    [dict(attempt) for attempt in wave]
                    for wave in checkpoint.launch_waves
                ],
                "updated_at": checkpoint.updated_at,
            },
        )
        return str(path)

    def write_integrated_diff(self, diff_text: str) -> str:
        """保存所有成功 worker 已合入集成 workspace 的最终 diff。"""

        path = self.root / "integrated_changes.diff"
        path.write_text(diff_text, encoding="utf-8")
        return str(path)

    def write_summary(self, summary: FanoutSummary) -> None:
        summary_path = self.root / "fanout_summary.json"
        report_path = self.root / "fanout_report.md"
        summary.summary_path = str(summary_path)
        summary.report_path = str(report_path)
        atomic_write_json(summary_path, summary.to_dict())
        report_path.write_text(
            render_fanout_report(summary),
            encoding="utf-8",
        )
    # endregion 1. Canonical 写入结束

    # region 2. Resume 与只读 artifact：返回未信任 mapping，由 Application 继续校验
    def load_resume(self, path: str) -> dict[str, Any]:
        """只定位 canonical Checkpoint；Summary 从不拥有恢复 authority。"""

        resume_path = _resolve_resume_artifact(Path(path))
        data = json.loads(resume_path.read_text(encoding="utf-8"))
        # Application 需要 mapping 才能继续做 schema/plan digest/Git base 校验。
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


# region 4. 路径解析：显式文件或 canonical fanout checkpoint
def _resolve_resume_artifact(path: Path) -> Path:
    """按稳定优先级解析显式文件、fanout 子目录或 Run 根目录。"""

    # 调用方直接给出文件时原样使用，不猜其他 latest 路径。
    if path.is_file():
        if path.name != "fanout_checkpoint.json":
            raise ValueError("fanout resume accepts fanout_checkpoint.json only")
        return path
    candidate = path / "fanout" / "fanout_checkpoint.json"
    if candidate.exists():
        return candidate
    candidate = path / "fanout_checkpoint.json"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"no fanout checkpoint found under {path}")
# endregion 4. 路径解析结束
