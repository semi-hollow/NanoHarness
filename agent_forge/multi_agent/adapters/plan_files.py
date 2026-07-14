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
