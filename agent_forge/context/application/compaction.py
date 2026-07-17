"""完整模型请求的预算估算与结构化会话压缩。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import TypeVar

from agent_forge.context.domain import SessionDigest, ToolTransactionDigest
from agent_forge.context.token_budget import truncate_middle
from agent_forge.contracts import ToolSchema
from agent_forge.runtime.domain.conversation import Message, Observation


T = TypeVar("T")


# 核心数据：一次模型请求允许使用的输入窗口与输出预留预算。
@dataclass(frozen=True)
class PromptBudget:
    """模型窗口中输入与预留输出的显式预算。

    ``max_prompt_tokens`` 是总窗口上限；``reserved_output_tokens`` 先为模型输出
    留位；``soft_limit_ratio`` 决定主动压缩阈值；``chars_per_token`` 只用于调用前
    近似估算，不冒充 provider tokenizer 的精确结果。
    """

    max_prompt_tokens: int = 32_768
    reserved_output_tokens: int = 4_096
    soft_limit_ratio: float = 0.8
    chars_per_token: float = 4.0

    def __post_init__(self) -> None:
        """拒绝会让输入预算变成伪值的配置。"""

        if self.max_prompt_tokens <= 0:
            raise ValueError("max_prompt_tokens must be positive")
        if self.reserved_output_tokens < 0:
            raise ValueError("reserved_output_tokens cannot be negative")
        if self.reserved_output_tokens >= self.max_prompt_tokens:
            raise ValueError(
                "reserved_output_tokens must be smaller than max_prompt_tokens"
            )
        if not 0.0 < self.soft_limit_ratio <= 1.0:
            raise ValueError("soft_limit_ratio must be between 0 and 1")
        if self.chars_per_token <= 0:
            raise ValueError("chars_per_token must be positive")

    @property
    def hard_input_limit(self) -> int:
        return max(256, self.max_prompt_tokens - self.reserved_output_tokens)

    @property
    def soft_input_limit(self) -> int:
        return max(256, int(self.hard_input_limit * self.soft_limit_ratio))


# 核心数据：上下文治理后真正发送给模型的消息与压缩证据。
@dataclass(frozen=True)
class ContextWindowResult:
    """一次预算决策产生的最终消息和可审计度量。

    ``messages`` 是最终模型输入；``digest`` 是可选压缩摘要；``compacted`` 和
    ``reason`` 解释是否压缩；其余计数字段记录覆盖消息数、压缩前后估算与
    硬上限。
    """

    messages: list[Message]
    digest: SessionDigest | None
    compacted: bool
    covered_message_count: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    hard_input_limit: int
    reason: str


@dataclass(frozen=True)
class _HistorySegment:
    """不可拆分的历史单元；工具意图和结果必须留在同一段。"""

    messages: list[Message]
    observations: list[Observation | None]


class ContextWindowManager:
    """在 LLM 边界前控制完整请求，而不删除原始 session/trace。"""

    def __init__(self, budget: PromptBudget) -> None:
        self.budget = budget

    # 主要入口：预算足够时直通，接近窗口时压缩旧历史。
    def prepare(
        self,
        *,
        system_message: Message,
        history: list[Message],
        observations: list[Observation],
        tools: list[ToolSchema],
        task: str,
        force_compaction: bool = False,
    ) -> ContextWindowResult:
        """返回不拆分工具事务的模型输入视图。"""

        full_messages = [system_message, *history]
        before = estimate_prompt_tokens(full_messages, tools, self.budget)
        if before <= self.budget.soft_input_limit and not force_compaction:
            return ContextWindowResult(
                messages=full_messages,
                digest=None,
                compacted=False,
                covered_message_count=0,
                estimated_tokens_before=before,
                estimated_tokens_after=before,
                hard_input_limit=self.budget.hard_input_limit,
                reason="within_soft_limit",
            )

        segments = _segments(history, observations)
        if len(segments) < 2:
            return ContextWindowResult(
                messages=full_messages,
                digest=None,
                compacted=False,
                covered_message_count=0,
                estimated_tokens_before=before,
                estimated_tokens_after=before,
                hard_input_limit=self.budget.hard_input_limit,
                reason="insufficient_history_to_compact",
            )

        target = (
            max(256, int(self.budget.soft_input_limit * 0.65))
            if force_compaction
            else self.budget.soft_input_limit
        )
        best: ContextWindowResult | None = None
        for cut in range(1, len(segments)):
            omitted = segments[:cut]
            recent = _flatten(segments[cut:])
            recent = _trim_large_messages(
                recent,
                max_chars=800 if force_compaction else 2_000,
            )
            digest = _build_digest(
                task,
                omitted,
                estimated_tokens_before=before,
            )
            candidate = [
                system_message,
                Message("system", digest.render()),
                *recent,
            ]
            after = estimate_prompt_tokens(candidate, tools, self.budget)
            digest = replace(digest, estimated_tokens_after=after)
            candidate[1] = Message("system", digest.render())
            after = estimate_prompt_tokens(candidate, tools, self.budget)
            result = ContextWindowResult(
                messages=candidate,
                digest=replace(digest, estimated_tokens_after=after),
                compacted=True,
                covered_message_count=digest.covered_message_count,
                estimated_tokens_before=before,
                estimated_tokens_after=after,
                hard_input_limit=self.budget.hard_input_limit,
                reason=(
                    "provider_overflow_recovery"
                    if force_compaction
                    else "soft_limit_exceeded"
                ),
            )
            if best is None or after < best.estimated_tokens_after:
                best = result
            if after <= target and after < before:
                return result

        if best is not None and best.estimated_tokens_after < before:
            return best
        return ContextWindowResult(
            messages=full_messages,
            digest=None,
            compacted=False,
            covered_message_count=0,
            estimated_tokens_before=before,
            estimated_tokens_after=before,
            hard_input_limit=self.budget.hard_input_limit,
            reason="no_safe_compaction_boundary",
        )


def estimate_prompt_tokens(
    messages: list[Message],
    tools: list[ToolSchema],
    budget: PromptBudget,
) -> int:
    """用统一近似估算完整请求；provider usage 仍是事后权威值。"""

    chars = 0
    for message in messages:
        chars += len(message.role) + len(message.content or "")
        chars += len(message.name or "") + len(message.tool_call_id or "")
        chars += len(message.reasoning_content or "")
        chars += len(json.dumps(message.tool_calls or [], ensure_ascii=False))
        chars += 16
    chars += sum(len(json.dumps(tool, ensure_ascii=False)) + 24 for tool in tools)
    return max(1, int(chars / max(1.0, budget.chars_per_token)))


def _segments(
    messages: list[Message],
    observations: list[Observation],
) -> list[_HistorySegment]:
    observation_index = 0
    segments: list[_HistorySegment] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        segment_messages = [message]
        segment_observations: list[Observation | None] = [None]
        index += 1
        if message.role == "assistant" and message.tool_calls:
            while index < len(messages) and messages[index].role == "tool":
                segment_messages.append(messages[index])
                observation = (
                    observations[observation_index]
                    if observation_index < len(observations)
                    else None
                )
                segment_observations.append(observation)
                observation_index += 1
                index += 1
        elif message.role == "tool":
            observation = (
                observations[observation_index]
                if observation_index < len(observations)
                else None
            )
            segment_observations[0] = observation
            observation_index += 1
        segments.append(_HistorySegment(segment_messages, segment_observations))
    return segments


def _flatten(segments: list[_HistorySegment]) -> list[Message]:
    return [message for segment in segments for message in segment.messages]


def _build_digest(
    task: str,
    segments: list[_HistorySegment],
    *,
    estimated_tokens_before: int,
) -> SessionDigest:
    messages = [message for segment in segments for message in segment.messages]
    source = json.dumps(
        [
            {
                "role": message.role,
                "content": message.content,
                "name": message.name,
                "tool_call_id": message.tool_call_id,
                "tool_calls": message.tool_calls,
            }
            for message in messages
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    user_updates = _bounded(
        [
            _excerpt(message.content, 320)
            for message in messages
            if message.role == "user" and message.content.strip() != task.strip()
        ],
        6,
    )
    assistant_updates = _bounded(
        [
            _excerpt(message.content, 320)
            for message in messages
            if message.role == "assistant" and message.content.strip()
        ],
        6,
    )
    transactions: list[ToolTransactionDigest] = []
    open_failures: list[str] = []
    for segment in segments:
        tool_messages = {
            message.tool_call_id: (message, observation)
            for message, observation in zip(
                segment.messages,
                segment.observations,
            )
            if message.role == "tool"
        }
        for message in segment.messages:
            if message.role != "assistant":
                continue
            for call in message.tool_calls or []:
                call_id = str(call.get("id") or "")
                function = call.get("function") if isinstance(call, dict) else None
                if isinstance(function, dict):
                    tool_name = str(function.get("name") or "unknown")
                    arguments = function.get("arguments") or ""
                else:
                    tool_name = str(call.get("name") or "unknown")
                    arguments = call.get("arguments") or ""
                tool_message, observation = tool_messages.get(call_id, (None, None))
                content = tool_message.content if tool_message is not None else ""
                success = observation.success if observation is not None else None
                transaction = ToolTransactionDigest(
                    tool_name=tool_name,
                    arguments_summary=_excerpt(_stable_text(arguments), 220),
                    success=success,
                    observation_excerpt=_excerpt(content, 320),
                )
                transactions.append(transaction)
                if success is False:
                    open_failures.append(
                        f"{tool_name}: {_excerpt(content, 240)}"
                    )
    return SessionDigest(
        task=task,
        covered_message_count=len(messages),
        source_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        user_updates=user_updates,
        tool_transactions=_bounded(transactions, 16),
        assistant_updates=assistant_updates,
        open_failures=_bounded(open_failures, 8),
        estimated_tokens_before=estimated_tokens_before,
        estimated_tokens_after=0,
    )


def _trim_large_messages(messages: list[Message], max_chars: int) -> list[Message]:
    trimmed: list[Message] = []
    for message in messages:
        trimmed.append(
            Message(
                role=message.role,
                content=truncate_middle(message.content or "", max_chars),
                name=message.name,
                tool_call_id=message.tool_call_id,
                tool_calls=message.tool_calls,
                reasoning_content=(
                    truncate_middle(message.reasoning_content, max_chars)
                    if message.reasoning_content
                    else None
                ),
            )
        )
    return trimmed


def _stable_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _excerpt(text: str, max_chars: int) -> str:
    return truncate_middle(" ".join(text.split()), max_chars)


def _bounded(values: list[T], limit: int) -> list[T]:
    """同时保留最早事实和最近状态，避免只看尾部。"""

    if len(values) <= limit:
        return list(values)
    head = max(1, limit // 3)
    return [*values[:head], *values[-(limit - head) :]]
