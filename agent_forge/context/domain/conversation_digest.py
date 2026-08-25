"""被移出模型窗口的 Conversation History 的确定性 continuation projection。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from agent_forge.contracts import JsonObject


CONVERSATION_DIGEST_SCHEMA_VERSION = 2
TOOL_STATE_STATUSES = frozenset(
    {"passed", "failed", "blocked", "unknown", "stale"}
)
AUTHORITY_UPDATE_MAX_CHARS = 480
RECENT_TOOL_TRANSACTION_LIMIT = 8
RESOURCE_HINT_LIMIT = 24
RESOURCE_HINT_MAX_CHARS = 160
STATE_EVIDENCE_LIMIT = 32
STATE_ARGUMENTS_MAX_CHARS = 220
STATE_OBSERVATION_MAX_CHARS = 320


@dataclass(frozen=True)
class ToolTransactionDigest:
    """最近一次工具意图与结果的 breadcrumb；不是 durable semantic memory。"""

    tool_name: str
    capability: str
    mode: str
    arguments_summary: str
    success: bool | None
    observation_excerpt: str

    def __post_init__(self) -> None:
        if not self.tool_name or not self.capability or not self.mode:
            raise ValueError("tool transaction digest identity is incomplete")
        if (
            len(self.arguments_summary) > STATE_ARGUMENTS_MAX_CHARS
            or len(self.observation_excerpt) > STATE_OBSERVATION_MAX_CHARS
        ):
            raise ValueError("tool transaction digest excerpt is oversized")

    def to_dict(self) -> JsonObject:
        return {
            "tool_name": self.tool_name,
            "capability": self.capability,
            "mode": self.mode,
            "arguments_summary": self.arguments_summary,
            "success": self.success,
            "observation_excerpt": self.observation_excerpt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolTransactionDigest":
        """从 Context State 恢复一条 recent breadcrumb。"""

        raw_success = data.get("success")
        success = raw_success if isinstance(raw_success, bool) else None
        tool_name = str(data.get("tool_name") or "")
        capability = str(data.get("capability") or "")
        mode = str(data.get("mode") or "")
        if not tool_name or not capability or not mode:
            raise ValueError("tool transaction digest identity is incomplete")
        return cls(
            tool_name=tool_name,
            capability=capability,
            mode=mode,
            arguments_summary=str(data.get("arguments_summary") or ""),
            success=success,
            observation_excerpt=str(data.get("observation_excerpt") or ""),
        )


@dataclass(frozen=True)
class ToolStateDigest:
    """一个 deterministic validation/command key 的最新已知状态。"""

    state_key: str
    tool_name: str
    capability: str
    arguments_summary: str
    status: str
    observation_excerpt: str

    def __post_init__(self) -> None:
        if not self.state_key or not self.tool_name or not self.capability:
            raise ValueError("tool state digest identity is incomplete")
        if self.status not in TOOL_STATE_STATUSES:
            raise ValueError(f"unsupported tool state status: {self.status}")
        if (
            len(self.arguments_summary) > STATE_ARGUMENTS_MAX_CHARS
            or len(self.observation_excerpt) > STATE_OBSERVATION_MAX_CHARS
        ):
            raise ValueError("tool state digest excerpt is oversized")

    def to_dict(self) -> JsonObject:
        return {
            "state_key": self.state_key,
            "tool_name": self.tool_name,
            "capability": self.capability,
            "arguments_summary": self.arguments_summary,
            "status": self.status,
            "observation_excerpt": self.observation_excerpt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolStateDigest":
        """从 Context State 恢复一个 latest-value-wins 状态。"""

        return cls(
            state_key=str(data.get("state_key") or ""),
            tool_name=str(data.get("tool_name") or ""),
            capability=str(data.get("capability") or ""),
            arguments_summary=str(data.get("arguments_summary") or ""),
            status=str(data.get("status") or "unknown"),
            observation_excerpt=str(data.get("observation_excerpt") or ""),
        )


@dataclass(frozen=True)
class ConversationHistoryDigest:
    """旧 Conversation 的 deterministic continuation projection。

    ``conversation.jsonl`` 才是无损 authority。本对象只长期保留 human authority
    与 keyed current state；可重建 Tool event 只作为有界 recent breadcrumbs。
    ``unresolved_failures`` 从 ``state_evidence`` 派生，避免持久化两份可能漂移的状态。
    """

    initial_task: str
    covered_message_count: int
    source_hash: str
    authority_updates: list[str]
    resource_hints: list[str]
    state_evidence: list[ToolStateDigest]
    recent_tool_transactions: list[ToolTransactionDigest]
    estimated_tokens_before: int
    estimated_tokens_after: int
    workspace_mutation_observed: bool = False
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.initial_task or not self.source_hash:
            raise ValueError("conversation history digest identity is incomplete")
        if self.covered_message_count < 0:
            raise ValueError("conversation history digest covered count must not be negative")
        if any(len(item) > AUTHORITY_UPDATE_MAX_CHARS for item in self.authority_updates):
            raise ValueError("conversation history digest authority excerpt is oversized")
        if len(self.resource_hints) > RESOURCE_HINT_LIMIT or any(
            len(item) > RESOURCE_HINT_MAX_CHARS for item in self.resource_hints
        ):
            raise ValueError("conversation history digest resource hints are oversized")
        if len(self.recent_tool_transactions) > RECENT_TOOL_TRANSACTION_LIMIT:
            raise ValueError("conversation history digest recent transactions are oversized")
        state_keys = [item.state_key for item in self.state_evidence]
        if len(state_keys) != len(set(state_keys)):
            raise ValueError("conversation history digest has duplicate state keys")

    @property
    def unresolved_failures(self) -> list[ToolStateDigest]:
        """返回当前仍未通过的 keyed state；它不是第二份持久化 authority。"""

        return [item for item in self.state_evidence if item.status != "passed"]

    @property
    def authority_context_chars(self) -> int:
        """用于 deterministic fail-closed capacity check 的 authority 字符数。"""

        return len(self.initial_task) + sum(len(item) for item in self.authority_updates)

    @property
    def state_context_chars(self) -> int:
        """估算 persisted current-state projection 的字符占用。"""

        return sum(
            len(item.state_key)
            + len(item.tool_name)
            + len(item.capability)
            + len(item.arguments_summary)
            + len(item.status)
            + len(item.observation_excerpt)
            for item in self.state_evidence
        )

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": CONVERSATION_DIGEST_SCHEMA_VERSION,
            "initial_task": self.initial_task,
            "covered_message_count": self.covered_message_count,
            "source_hash": self.source_hash,
            "authority_updates": list(self.authority_updates),
            "resource_hints": list(self.resource_hints),
            "state_evidence": [item.to_dict() for item in self.state_evidence],
            "recent_tool_transactions": [
                item.to_dict() for item in self.recent_tool_transactions
            ],
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
            "workspace_mutation_observed": self.workspace_mutation_observed,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> "ConversationHistoryDigest":
        """从 Thread Context State 恢复 canonical V2 continuation projection。

        这是 clean-break schema boundary：旧的 task/tool/failure position-retention
        字段不会被静默解释成新语义。
        """

        if int(data.get("schema_version") or 0) != CONVERSATION_DIGEST_SCHEMA_VERSION:
            raise ValueError("unsupported conversation history digest schema_version")
        source_hash = str(data.get("source_hash") or "")
        initial_task = str(data.get("initial_task") or "")
        if not source_hash or not initial_task:
            raise ValueError("conversation history digest identity is incomplete")
        authority_payloads = data.get("authority_updates")
        resource_payloads = data.get("resource_hints")
        state_payloads = data.get("state_evidence")
        transaction_payloads = data.get("recent_tool_transactions")
        for name, payload in (
            ("authority_updates", authority_payloads),
            ("resource_hints", resource_payloads),
            ("state_evidence", state_payloads),
            ("recent_tool_transactions", transaction_payloads),
        ):
            if not isinstance(payload, list):
                raise ValueError(f"conversation history digest {name} must be a list")
        assert isinstance(authority_payloads, list)
        assert isinstance(resource_payloads, list)
        assert isinstance(state_payloads, list)
        assert isinstance(transaction_payloads, list)
        if not all(isinstance(item, str) for item in authority_payloads):
            raise ValueError("conversation history digest authority updates must be strings")
        if not all(isinstance(item, str) for item in resource_payloads):
            raise ValueError("conversation history digest resource hints must be strings")
        if not all(isinstance(item, dict) for item in state_payloads):
            raise ValueError("conversation history digest state evidence is malformed")
        if not all(isinstance(item, dict) for item in transaction_payloads):
            raise ValueError("conversation history digest recent transactions are malformed")
        raw_workspace_mutation_observed = data.get("workspace_mutation_observed")
        if not isinstance(raw_workspace_mutation_observed, bool):
            raise ValueError(
                "conversation history digest workspace mutation flag must be boolean"
            )
        return cls(
            initial_task=initial_task,
            covered_message_count=max(
                0,
                int(data.get("covered_message_count") or 0),
            ),
            source_hash=source_hash,
            authority_updates=list(authority_payloads),
            resource_hints=list(resource_payloads),
            state_evidence=[
                ToolStateDigest.from_dict(item)
                for item in state_payloads
            ],
            recent_tool_transactions=[
                ToolTransactionDigest.from_dict(item)
                for item in transaction_payloads
            ],
            estimated_tokens_before=max(
                0,
                int(data.get("estimated_tokens_before") or 0),
            ),
            estimated_tokens_after=max(
                0,
                int(data.get("estimated_tokens_after") or 0),
            ),
            workspace_mutation_observed=raw_workspace_mutation_observed,
            created_at=float(data.get("created_at") or time.time()),
        )

    def render(self) -> str:
        """渲染模型可见 continuation state，不把它冒充 raw history。"""

        authority = "\n".join(f"- {item}" for item in self.authority_updates)
        state = "\n".join(
            "- "
            f"[{item.status}] {item.state_key} "
            f"{item.tool_name}({item.arguments_summary}): {item.observation_excerpt}"
            for item in self.state_evidence
        )
        transactions = "\n".join(
            "- "
            f"{item.tool_name}[{item.capability}/{item.mode}]({item.arguments_summary}) -> "
            f"{'ok' if item.success is True else 'fail' if item.success is False else 'unknown'}: "
            f"{item.observation_excerpt}"
            for item in self.recent_tool_transactions
        )
        unresolved_keys = [item.state_key for item in self.unresolved_failures]
        return "\n".join(
            [
                "conversation_history_digest "
                "(derived continuation projection; raw journal remains authority):",
                f"initial_task: {self.initial_task}",
                f"covered_messages: {self.covered_message_count}",
                "authority_updates:",
                authority or "- none",
                f"resource_hints: {self.resource_hints}",
                "state_evidence (latest value per deterministic key):",
                state or "- none",
                f"unresolved_failures: {unresolved_keys}",
                "recent_tool_transactions (breadcrumbs only):",
                transactions or "- none",
                f"source_hash: {self.source_hash}",
            ]
        )
