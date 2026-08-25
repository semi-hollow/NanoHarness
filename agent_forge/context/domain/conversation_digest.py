"""被移出模型窗口的 Conversation History 的确定性 continuation projection。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from agent_forge.contracts import JsonObject


CONVERSATION_DIGEST_SCHEMA_VERSION = 4
TOOL_STATE_STATUSES = frozenset({"passed", "failed", "blocked", "unknown"})
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
    arguments_summary: str
    success: bool | None
    observation_excerpt: str

    def __post_init__(self) -> None:
        if not self.tool_name:
            raise ValueError("tool transaction digest tool_name is missing")
        if (
            len(self.arguments_summary) > STATE_ARGUMENTS_MAX_CHARS
            or len(self.observation_excerpt) > STATE_OBSERVATION_MAX_CHARS
        ):
            raise ValueError("tool transaction digest excerpt is oversized")

    def to_dict(self) -> JsonObject:
        return {
            "tool_name": self.tool_name,
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
        if not tool_name:
            raise ValueError("tool transaction digest tool_name is missing")
        return cls(
            tool_name=tool_name,
            arguments_summary=str(data.get("arguments_summary") or ""),
            success=success,
            observation_excerpt=str(data.get("observation_excerpt") or ""),
        )


@dataclass(frozen=True)
class ToolStateDigest:
    """一个结构化 Python validation contract 的最新已知状态。"""

    state_key: str
    tool_name: str
    check_type: str
    validation_target: str
    status: str
    observation_excerpt: str

    def __post_init__(self) -> None:
        if not self.state_key or not self.tool_name or not self.check_type:
            raise ValueError("tool state digest identity is incomplete")
        if self.status not in TOOL_STATE_STATUSES:
            raise ValueError(f"unsupported tool state status: {self.status}")
        if (
            len(self.check_type) + len(self.validation_target)
            > STATE_ARGUMENTS_MAX_CHARS
            or len(self.observation_excerpt) > STATE_OBSERVATION_MAX_CHARS
        ):
            raise ValueError("tool state digest excerpt is oversized")

    def to_dict(self) -> JsonObject:
        return {
            "state_key": self.state_key,
            "tool_name": self.tool_name,
            "check_type": self.check_type,
            "validation_target": self.validation_target,
            "status": self.status,
            "observation_excerpt": self.observation_excerpt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolStateDigest":
        """从 Context State 恢复一个 latest-value-wins 状态。"""

        return cls(
            state_key=str(data.get("state_key") or ""),
            tool_name=str(data.get("tool_name") or ""),
            check_type=str(data.get("check_type") or ""),
            validation_target=str(data.get("validation_target") or "."),
            status=str(data.get("status") or "unknown"),
            observation_excerpt=str(data.get("observation_excerpt") or ""),
        )


@dataclass(frozen=True)
class ConversationHistoryDigest:
    """旧 Conversation 的 deterministic continuation projection。

    ``conversation.jsonl`` 才是无损 authority。本对象只保留当前 Turn 的后续 human
    authority 与 keyed validation state；Turn.root_task 由 Turn/StableTurnContextSnapshot
    拥有，不复制进 digest。可重建 Tool event 只作为有界 recent breadcrumbs。

    State 只描述截至 ``ThreadContextState.covered_sequence`` 的 compacted prefix；
    后面的 protocol-preserving recent tail 可能按时间顺序覆盖这里的状态。
    """

    authority_turn_id: str
    covered_message_count: int
    source_hash: str
    authority_updates: list[str]
    resource_hints: list[str]
    state_evidence: list[ToolStateDigest]
    recent_tool_transactions: list[ToolTransactionDigest]
    estimated_tokens_before: int
    estimated_tokens_after: int
    # 仅在一次 rolling merge 内传递“先丢弃 previous validation”的增量命令；它既不
    # 持久化，也不渲染给模型。落盘前 state_evidence 已经反映 invalidate 结果。
    invalidates_prior_validation: bool = field(
        default=False,
        compare=False,
        repr=False,
    )
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.authority_turn_id or not self.source_hash:
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
    def authority_context_chars(self) -> int:
        """用于 deterministic fail-closed capacity check 的 authority 字符数。"""

        return sum(len(item) for item in self.authority_updates)

    @property
    def state_context_chars(self) -> int:
        """估算 persisted current-state projection 的字符占用。"""

        return sum(
            len(item.state_key)
            + len(item.tool_name)
            + len(item.check_type)
            + len(item.validation_target)
            + len(item.status)
            + len(item.observation_excerpt)
            for item in self.state_evidence
        )

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": CONVERSATION_DIGEST_SCHEMA_VERSION,
            "authority_turn_id": self.authority_turn_id,
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
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> "ConversationHistoryDigest":
        """从 Thread Context State 恢复 canonical V4 continuation projection。

        这是 clean-break schema boundary：旧的 Thread-wide authority 与 generic command
        state/stale validation 不会被静默解释成当前 projection。
        """

        if int(data.get("schema_version") or 0) != CONVERSATION_DIGEST_SCHEMA_VERSION:
            raise ValueError("unsupported conversation history digest schema_version")
        source_hash = str(data.get("source_hash") or "")
        authority_turn_id = str(data.get("authority_turn_id") or "")
        if not source_hash or not authority_turn_id:
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
        return cls(
            authority_turn_id=authority_turn_id,
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
            created_at=float(data.get("created_at") or time.time()),
        )

    def render(self) -> str:
        """渲染模型可见 continuation state，不把它冒充 raw history。"""

        authority = "\n".join(f"- {item}" for item in self.authority_updates)
        state = "\n".join(
            "- "
            f"[{item.status}] {item.tool_name} "
            f"{item.check_type} {item.validation_target}: {item.observation_excerpt}"
            for item in self.state_evidence
        )
        transactions = "\n".join(
            "- "
            f"{item.tool_name}({item.arguments_summary}) -> "
            f"{'ok' if item.success is True else 'fail' if item.success is False else 'unknown'}: "
            f"{item.observation_excerpt}"
            for item in self.recent_tool_transactions
        )
        return "\n".join(
            [
                "conversation_history_digest "
                "(derived continuation projection; raw journal remains authority):",
                f"covered_messages: {self.covered_message_count}",
                f"current_turn_authority: {self.authority_turn_id}",
                "current_turn_authority_updates:",
                authority or "- none",
                f"resource_hints: {self.resource_hints}",
                "validation_state "
                "(as of compacted prefix; recent tail may supersede):",
                state or "- none",
                "recent_tool_transactions (breadcrumbs only):",
                transactions or "- none",
                f"source_hash: {self.source_hash}",
            ]
        )
