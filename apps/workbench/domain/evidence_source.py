"""Workbench 统一读取的运行证据来源。

预置场景、普通 ``forge run``、多 Agent Fanout 和 Benchmark 的产物形状不同，
但展示层只需要关心同一组事实：这次运行要解决什么、最终状态是什么，以及
Trace、Usage 和产物分别在哪里。这个读模型把文件布局差异挡在 Adapter 内。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, kw_only=True)
class EvidenceSource:
    """一项可在 Workbench 中选择的运行证据。

    ``source_type`` 只决定如何解释产物，不代表能力等级。``scenario`` 表示项目自带的
    可复现场景，``runtime`` 表示任意 Runtime 运行，``benchmark`` 表示批量评测；
    三者都必须遵守相同的证据边界。
    """

    key: str
    title: str
    description: str
    source_type: str
    task: str
    status: str
    primary_path: Path | None
    run_dir: Path | None
    trace_entries: tuple[tuple[str, Path], ...] = ()
    usage_path: Path | None = None
    # 浏览器按“能力类型 → 不可变 Run → Case/Worker”分层导航；key 仍唯一定位叶子。
    category_key: str = ""
    category_title: str = ""
    run_key: str = ""
    run_title: str = ""
    item_key: str = ""
    item_title: str = ""

    @property
    def available(self) -> bool:
        """是否存在足以打开该运行的主产物。"""

        return self.primary_path is not None and self.primary_path.exists()

    def to_public_dict(self) -> dict[str, object]:
        """返回浏览器选择器所需的精简字段，不泄漏整份 Trace。"""

        return {
            "key": self.key,
            "title": self.title,
            "description": self.description,
            "source_type": self.source_type,
            "task": self.task,
            "status": self.status,
            "available": self.available,
            "trace_count": len(self.trace_entries),
            "primary_path": str(self.primary_path or ""),
            "category_key": self.category_key or self.key,
            "category_title": self.category_title or self.title,
            "run_key": self.run_key or self.key,
            "run_title": self.run_title or self.title,
            "item_key": self.item_key or "overview",
            "item_title": self.item_title or "整体运行",
        }
