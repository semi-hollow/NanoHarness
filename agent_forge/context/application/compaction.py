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
@dataclass(frozen=True, kw_only=True)
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
@dataclass(frozen=True, kw_only=True)
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


# 核心数据：完整模型请求进入窗口治理前的输入快照。
@dataclass(frozen=True, kw_only=True)
class ContextWindowRequest:
    """System、历史、Observation、工具和强制压缩信号。"""

    system_message: Message
    history: list[Message]
    observations: list[Observation]
    tools: list[ToolSchema]
    task: str
    force_compaction: bool = False


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
    def prepare(self, request: ContextWindowRequest) -> ContextWindowResult:
        """在模型调用前生成满足硬窗口限制的、可解释的消息视图。

        规范上游是 ``TurnPreparation``；下一 owner 是 ``ModelPort``。返回值同时
        携带压缩前后 token 估算、覆盖范围和原因，供 trace 形成上下文证据。系统
        不变量是 system message 必须保留，assistant/tool 事务不得拆分，无法安全
        压缩时不得伪称已经满足 hard limit。
        """

        # region 1. 预算判定：先估算完整请求，未超软限制时保持原文
        # 估算覆盖 system、完整历史和 Tool schema；软限制以内直接透传，
        # force_compaction 仅用于 Provider 已明确拒绝当前窗口后的恢复重试。
        full_messages = [request.system_message, *request.history]
        estimated_tokens_before = estimate_prompt_tokens(
            full_messages,
            request.tools,
            self.budget,
        )
        if (
            estimated_tokens_before <= self.budget.soft_input_limit
            and not request.force_compaction
        ):
            return ContextWindowResult(
                messages=full_messages,
                digest=None,
                compacted=False,
                covered_message_count=0,
                estimated_tokens_before=estimated_tokens_before,
                estimated_tokens_after=estimated_tokens_before,
                hard_input_limit=self.budget.hard_input_limit,
                reason="within_soft_limit",
            )
        # endregion 1. 预算判定结束

        # region 2. 安全切分：assistant ToolCall 与对应 tool Observation 不可拆散
        # 历史先按协议事务分组，再选择摘要边界；少于两个 segment 时没有“旧历史”
        # 可以安全替换，所以宁可报告无法压缩，也不拆坏 ToolCall 对应关系。
        history_segments = _group_history_segments(
            request.history,
            request.observations,
        )
        if len(history_segments) < 2:
            return ContextWindowResult(
                messages=full_messages,
                digest=None,
                compacted=False,
                covered_message_count=0,
                estimated_tokens_before=estimated_tokens_before,
                estimated_tokens_after=estimated_tokens_before,
                hard_input_limit=self.budget.hard_input_limit,
                reason="insufficient_history_to_compact",
            )
        # endregion 2. 安全切分结束

        # region 3. 候选搜索：逐步扩大旧历史摘要范围，保留最新原始消息
        # cut_index 从最小旧历史开始递增；每个候选都重建摘要并重新估算 token，
        # 第一个达到目标且确实变小的候选立即返回，同时保留最小候选作为保守回退。
        target_token_count = (
            max(256, int(self.budget.soft_input_limit * 0.65))
            if request.force_compaction
            else self.budget.soft_input_limit
        )
        best_compaction_result: ContextWindowResult | None = None
        for cut_index in range(1, len(history_segments)):
            omitted_segments = history_segments[:cut_index]
            recent_messages = _flatten(history_segments[cut_index:])
            recent_messages = _trim_large_messages(
                recent_messages,
                max_chars=800 if request.force_compaction else 2_000,
            )
            session_digest = _build_digest(
                request.task,
                omitted_segments,
                estimated_tokens_before=estimated_tokens_before,
            )
            candidate_messages = [
                request.system_message,
                Message(role="system", content=session_digest.render()),
                *recent_messages,
            ]
            estimated_tokens_after = estimate_prompt_tokens(
                candidate_messages,
                request.tools,
                self.budget,
            )
            session_digest = replace(
                session_digest,
                estimated_tokens_after=estimated_tokens_after,
            )
            candidate_messages[1] = Message(
                role="system",
                content=session_digest.render(),
            )
            estimated_tokens_after = estimate_prompt_tokens(
                candidate_messages,
                request.tools,
                self.budget,
            )
            candidate_window_result = ContextWindowResult(
                messages=candidate_messages,
                digest=replace(
                    session_digest,
                    estimated_tokens_after=estimated_tokens_after,
                ),
                compacted=True,
                covered_message_count=session_digest.covered_message_count,
                estimated_tokens_before=estimated_tokens_before,
                estimated_tokens_after=estimated_tokens_after,
                hard_input_limit=self.budget.hard_input_limit,
                reason=(
                    "provider_overflow_recovery"
                    if request.force_compaction
                    else "soft_limit_exceeded"
                ),
            )
            if (
                best_compaction_result is None
                or estimated_tokens_after
                < best_compaction_result.estimated_tokens_after
            ):
                # 记录所有已尝试候选中最小的一份，供未达到软目标时做降级选择。
                best_compaction_result = candidate_window_result
            if (
                estimated_tokens_after <= target_token_count
                and estimated_tokens_after < estimated_tokens_before
            ):
                return candidate_window_result
        # endregion 3. 候选搜索结束

        # region 4. 保守回退：只接受真实缩小的结果，否则明确报告无法安全压缩
        if (
            best_compaction_result is not None
            and best_compaction_result.estimated_tokens_after < estimated_tokens_before
        ):
            return best_compaction_result
        return ContextWindowResult(
            messages=full_messages,
            digest=None,
            compacted=False,
            covered_message_count=0,
            estimated_tokens_before=estimated_tokens_before,
            estimated_tokens_after=estimated_tokens_before,
            hard_input_limit=self.budget.hard_input_limit,
            reason="no_safe_compaction_boundary",
        )
        # endregion 4. 保守回退结束


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


def _group_history_segments(
    messages: list[Message],
    observations: list[Observation],
) -> list[_HistorySegment]:
    """把一轮 assistant tool intent 与随后的 tool 结果绑定成不可拆分单元。"""

    observation_index = 0
    grouped_history_segments: list[_HistorySegment] = []
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
        grouped_history_segments.append(
            _HistorySegment(
                messages=segment_messages,
                observations=segment_observations,
            )
        )
    return grouped_history_segments


def _flatten(history_segments: list[_HistorySegment]) -> list[Message]:
    return [
        message
        for history_segment in history_segments
        for message in history_segment.messages
    ]


def _build_digest(
    task: str,
    history_segments: list[_HistorySegment],
    *,
    estimated_tokens_before: int,
) -> SessionDigest:
    covered_messages = [
        message
        for history_segment in history_segments
        for message in history_segment.messages
    ]
    digest_source_payload = json.dumps(
        [
            {
                "role": message.role,
                "content": message.content,
                "name": message.name,
                "tool_call_id": message.tool_call_id,
                "tool_calls": message.tool_calls,
            }
            for message in covered_messages
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    initial_user_message_seen = False
    task_updates: list[str] = []
    for message in covered_messages:
        if message.role != "user":
            continue
        if not initial_user_message_seen:
            initial_user_message_seen = True
            continue
        task_updates.append(_excerpt(message.content, 320))
    task_updates = _bounded(
        [
            update
            for update in task_updates
            if update
        ],
        6,
    )
    tool_transactions: list[ToolTransactionDigest] = []
    failed_tool_evidence: list[str] = []
    for history_segment in history_segments:
        tool_messages = {
            message.tool_call_id: (message, observation)
            for message, observation in zip(
                history_segment.messages,
                history_segment.observations,
            )
            if message.role == "tool"
        }
        for message in history_segment.messages:
            if message.role != "assistant":
                continue
            for tool_call_payload in message.tool_calls or []:
                tool_call_id = str(tool_call_payload.get("id") or "")
                function_payload = (
                    tool_call_payload.get("function")
                    if isinstance(tool_call_payload, dict)
                    else None
                )
                if isinstance(function_payload, dict):
                    tool_name = str(function_payload.get("name") or "unknown")
                    tool_arguments = function_payload.get("arguments") or ""
                else:
                    tool_name = str(tool_call_payload.get("name") or "unknown")
                    tool_arguments = tool_call_payload.get("arguments") or ""
                tool_message, observation = tool_messages.get(
                    tool_call_id,
                    (None, None),
                )
                tool_observation_content = (
                    tool_message.content if tool_message is not None else ""
                )
                tool_succeeded = (
                    observation.success if observation is not None else None
                )
                tool_transaction = ToolTransactionDigest(
                    tool_name=tool_name,
                    arguments_summary=_excerpt(
                        _stable_text(tool_arguments),
                        220,
                    ),
                    success=tool_succeeded,
                    observation_excerpt=_excerpt(
                        tool_observation_content,
                        320,
                    ),
                )
                tool_transactions.append(tool_transaction)
                if tool_succeeded is False:
                    failed_tool_evidence.append(
                        f"{tool_name}: {_excerpt(tool_observation_content, 240)}"
                    )
    return SessionDigest(
        task=task,
        covered_message_count=len(covered_messages),
        source_hash=hashlib.sha256(digest_source_payload.encode("utf-8")).hexdigest(),
        task_updates=task_updates,
        tool_transactions=_bounded(tool_transactions, 16),
        failed_tool_evidence=_bounded(failed_tool_evidence, 8),
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
    retained_prefix_count = max(1, limit // 3)
    return [
        *values[:retained_prefix_count],
        *values[-(limit - retained_prefix_count) :],
    ]
