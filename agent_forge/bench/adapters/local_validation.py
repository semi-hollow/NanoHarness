"""从 Runtime trace 投影本地 validation 状态。

这里只聚合 ``validation_evidence`` 事件；结果描述候选在本地记录的验证状态，既不是
official SWE-bench verdict，也不证明未被记录的测试已经运行。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LocalValidation:
    """一次 Case 的本地验证状态和有界证据摘要。"""

    status: str = "not_run"
    evidence: list[str] = field(default_factory=list)


def read_local_validation(trace_path: str | Path) -> LocalValidation:
    """读取 derived ``trace.json``，按保守优先级聚合 validation 事件。

    伪代码：读取 trace projection → 只选 validation_evidence → failed 优先
    → unavailable 次之 → 全部 passed 才通过 → 返回有界摘要。
    """

    # trace 缺失或损坏时只能说明没有可读本地证据，不能推断验证失败。
    try:
        trace = json.loads(Path(trace_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return LocalValidation()

    # region 1. 证据筛选：其他 RuntimeEvent 不参与 local validation 判定
    records = []
    for event in trace.get("events", []):
        if not isinstance(event, dict) or event.get("event_type") != "validation_evidence":
            continue
        validation = event.get("validation")
        if isinstance(validation, dict):
            records.append(validation)
    if not records:
        return LocalValidation()
    # endregion 1. 证据筛选结束

    # region 2. 保守聚合：任一 failed 覆盖 passed；混合/未知组合不能升级为 passed
    statuses = {str(record.get("status") or "") for record in records}
    if "failed" in statuses:
        status = "failed"
    elif "unavailable" in statuses:
        status = "unavailable"
    elif statuses == {"passed"}:
        status = "passed"
    else:
        status = "failed"
    evidence = [
        f"{record.get('kind', 'test')}:{record.get('status', 'unknown')}:{str(record.get('evidence') or '')[:300]}"
        for record in records
    ]
    return LocalValidation(status=status, evidence=evidence)
    # endregion 2. 保守聚合结束
