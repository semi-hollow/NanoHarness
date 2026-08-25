"""把持久化 JSON 边界转换成经过领域校验的 ``FanoutPlan``。

直接启动时读取显式计划文件；恢复时只定位 prior run 的 frozen FanoutPlan。
两条路径最终都进入 ``FanoutPlan.from_mapping``，因此文件层不复制 schema 规则，
也不会在恢复阶段重新调用 Planner。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..domain.fanout import FanoutPlan


def load_fanout_plan(path: str | Path) -> FanoutPlan:
    """读取 JSON 文件并立即转换为经过验证的领域计划。"""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    # 文件边界必须是 JSON object，所有字段/图规则继续交给 Domain。
    if not isinstance(data, dict):
        raise ValueError("fanout plan JSON must contain an object")
    return FanoutPlan.from_mapping(data)


def load_resume_plan(path: str | Path) -> FanoutPlan:
    """从 prior run 定位唯一 frozen FanoutPlan；不调用 Planner。

    伪代码：把文件或目录展开成有限候选 roots -> 按顺序查 ``fanout_plan.json``
    -> 找到后仍走 canonical loader -> 否则明确失败。
    """

    resume = Path(path)
    roots = [resume.parent, resume] if resume.is_file() else [resume / "fanout", resume]
    # 只搜索与显式 resume target 相邻的固定位置，不使用全局 latest。
    for root in roots:
        candidate = root / "fanout_plan.json"
        # 首个存在文件即进入完整 Domain 校验。
        if candidate.is_file():
            return load_fanout_plan(candidate)
    raise FileNotFoundError(f"no fanout_plan.json found for resume target: {path}")
