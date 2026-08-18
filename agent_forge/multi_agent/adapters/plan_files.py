"""Fanout 计划文件适配器。"""

from __future__ import annotations

import json
from pathlib import Path

from ..domain.live import FanoutPlan


def load_fanout_plan(path: str | Path) -> FanoutPlan:
    """读取 JSON 文件并立即转换为经过验证的领域计划。"""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("fanout plan JSON must contain an object")
    return FanoutPlan.from_mapping(data)


def load_resume_initial_plan(path: str | Path) -> FanoutPlan:
    """从 prior run 定位不可变 initial plan；不调用 Planner。"""

    resume = Path(path)
    roots = [resume.parent, resume] if resume.is_file() else [resume / "fanout", resume]
    for root in roots:
        candidate = root / "fanout_plan.json"
        if candidate.is_file():
            return load_fanout_plan(candidate)
    raise FileNotFoundError(f"no fanout_plan.json found for resume target: {path}")
