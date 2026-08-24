"""被移出模型窗口的 Conversation History 的有界摘要。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolTransactionDigest":
        """从 checkpoint 内嵌 JSON 恢复一条工具事务投影。"""

        raw_success = data.get("success")
        success = raw_success if isinstance(raw_success, bool) else None
        return cls(
            tool_name=str(data.get("tool_name") or "unknown"),
            arguments_summary=str(data.get("arguments_summary") or ""),
            success=success,
            observation_excerpt=str(data.get("observation_excerpt") or ""),
        )


@dataclass(frozen=True)
class ConversationHistoryDigest:
    """旧 Conversation 的确定性投影；raw authority 是 Thread journal。

    Trace 只保存 execution evidence，不是 raw Conversation store，也不能用于重新
    授予 human authority。
    """

    initial_task: str
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
            "initial_task": self.initial_task,
            "covered_message_count": self.covered_message_count,
            "source_hash": self.source_hash,
            "task_updates": list(self.task_updates),
            "tool_transactions": [item.to_dict() for item in self.tool_transactions],
            "failed_tool_evidence": list(self.failed_tool_evidence),
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> "ConversationHistoryDigest":
        """从 Thread context state 恢复可继续合并的 derived projection。

        ``ThreadContextState`` 是独立持久化 schema boundary；生产 loader 只接受
        当前 canonical ``initial_task``，不在运行时背旧字段 alias。
        """

        source_hash = str(data.get("source_hash") or "")
        if not source_hash:
            raise ValueError("conversation history digest requires source_hash")
        initial_task = str(data.get("initial_task") or "")
        if not initial_task:
            raise ValueError("conversation history digest requires initial_task")
        transaction_payloads = data.get("tool_transactions") or []
        if not isinstance(transaction_payloads, list):
            raise ValueError("conversation history digest tool_transactions must be a list")
        return cls(
            initial_task=initial_task,
            covered_message_count=max(
                0,
                int(data.get("covered_message_count") or 0),
            ),
            source_hash=source_hash,
            task_updates=[str(item) for item in data.get("task_updates") or []],
            tool_transactions=[
                ToolTransactionDigest.from_dict(item)
                for item in transaction_payloads
                if isinstance(item, dict)
            ],
            failed_tool_evidence=[
                str(item) for item in data.get("failed_tool_evidence") or []
            ],
            estimated_tokens_before=max(
                0,
                int(data.get("estimated_tokens_before") or 0),
            ),
            estimated_tokens_after=max(
                0,
                int(data.get("estimated_tokens_after") or 0),
            ),
            created_at=float(data.get("created_at") or time.time()),
        )

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
                "(derived continuation view; trace is evidence, not raw chat):",
                f"initial_task: {self.initial_task}",
                f"covered_messages: {self.covered_message_count}",
                f"task_updates: {self.task_updates}",
                f"failed_tool_evidence: {self.failed_tool_evidence}",
                "tool_transactions:",
                transactions or "- none",
                f"source_hash: {self.source_hash}",
            ]
        )
