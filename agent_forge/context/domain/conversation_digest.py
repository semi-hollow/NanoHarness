"""被移出模型窗口的 Conversation History 的有界摘要。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from agent_forge.contracts import JsonObject


@dataclass(frozen=True)
class ToolTransactionDigest:
    """压缩后仍保留的一次工具意图与结果摘要。"""

    tool_name: str
    arguments_summary: str
    success: bool | None
    observation_excerpt: str

    def to_dict(self) -> JsonObject:
        return {
            "tool_name": self.tool_name,
            "arguments_summary": self.arguments_summary,
            "success": self.success,
            "observation_excerpt": self.observation_excerpt,
        }


@dataclass(frozen=True)
class ConversationHistoryDigest:
    """旧 Conversation History 的确定性压缩投影，不替代原始 Trace。"""

    task: str
    covered_message_count: int
    source_hash: str
    task_updates: list[str]
    tool_transactions: list[ToolTransactionDigest]
    failed_tool_evidence: list[str]
    estimated_tokens_before: int
    estimated_tokens_after: int
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> JsonObject:
        return {
            "task": self.task,
            "covered_message_count": self.covered_message_count,
            "source_hash": self.source_hash,
            "task_updates": list(self.task_updates),
            "tool_transactions": [item.to_dict() for item in self.tool_transactions],
            "failed_tool_evidence": list(self.failed_tool_evidence),
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
            "created_at": self.created_at,
        }

    def render(self) -> str:
        transactions = "\n".join(
            "- "
            f"{item.tool_name}({item.arguments_summary}) -> "
            f"{'ok' if item.success is True else 'fail' if item.success is False else 'unknown'}: "
            f"{item.observation_excerpt}"
            for item in self.tool_transactions
        )
        return "\n".join(
            [
                "conversation_history_digest "
                "(summary only; raw trace remains authoritative):",
                f"task: {self.task}",
                f"covered_messages: {self.covered_message_count}",
                f"task_updates: {self.task_updates}",
                f"failed_tool_evidence: {self.failed_tool_evidence}",
                "tool_transactions:",
                transactions or "- none",
                f"source_hash: {self.source_hash}",
            ]
        )
