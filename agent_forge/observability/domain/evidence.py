"""从工具 Observation 选择少量可在最终回答中引用的 Run-local evidence。

``EvidenceLedger`` 是最终回答 citation 的轻量 read model：它不替代完整 Trace，
不承担 side-effect ``OperationLedger`` 的幂等/恢复职责，也不因收录文本就证明内容为真。

折叠导航：1 Evidence item；2 Observation selection；3 Final citations。
"""

from dataclasses import dataclass
from typing import Protocol

from agent_forge.contracts import WORKSPACE_WRITE_TOOL_NAMES


class ObservationView(Protocol):
    tool_name: str
    content: str
    success: bool


# region 1. Evidence item：来源、摘要、工具类型、观察成功状态
# 核心数据：从工具 Observation 提炼出的可引用证据条目。
@dataclass(frozen=True)
class EvidenceItem:
    """保存证据来源、摘要、类型和成功状态。"""

    source: str
    summary: str
    kind: str = "tool"
    success: bool = True

    def citation(self) -> str:
        status = "ok" if self.success else "fail"
        return f"{self.kind}:{self.source}:{status}:{self.summary}"
# endregion 1. Evidence item 结束


# 核心数据：单次 run 内按发生顺序累积的 EvidenceItem 集合。
class EvidenceLedger:
    """只提取值得进入最终回答的工具观察，不替代完整 Trace。"""

    # region 2. Observation selection：只收录可定位读写/验证事实或失败
    def __init__(self) -> None:
        self.items: list[EvidenceItem] = []

    def add_observation(self, observation: ObservationView) -> EvidenceItem | None:
        """按明确工具类别选择 citation；无关成功 Observation 返回 ``None``。

        伪代码：提取首行摘要 → 对 read/run/write/validation 使用稳定摘要
        → 保留所有失败 → 跳过其他成功事件 → 追加 run-local item。
        """

        text = observation.content or ""
        source = observation.tool_name
        summary = text.splitlines()[0][:160] if text else ""
        # read_file 把真实 path 提升为 citation source，避免最终回答只显示工具名。
        if observation.tool_name == "read_file" and text.startswith("path="):
            source = text.splitlines()[0].replace("path=", "", 1)
            summary = "file inspected"
        elif observation.tool_name == "run_command":
            summary = text.replace("\n", " ")[:160]
        elif observation.tool_name in WORKSPACE_WRITE_TOOL_NAMES:
            summary = text[:160]
        elif observation.tool_name in {
            "git_diff",
            "git_status",
            "python_validation",
        }:
            summary = text.replace("\n", " ")[:160]
        elif not observation.success:
            # 未列入白名单的失败仍值得引用，便于解释停止或恢复边界。
            summary = text[:160]
        else:
            # 其他成功事件留在完整 Trace，不把 citation ledger 扩成第二份事件流。
            return None

        item = EvidenceItem(
            source=source,
            summary=summary,
            kind=observation.tool_name,
            success=observation.success,
        )
        self.items.append(item)
        return item
    # endregion 2. Observation selection 结束

    # region 3. Final citations：按发生顺序截取最近 N 条，不重新排序或判真
    def final_citations(self, limit: int = 5) -> list[str]:
        return [item.citation() for item in self.items[-limit:]]
    # endregion 3. Final citations 结束
