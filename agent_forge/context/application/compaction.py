"""完整模型请求的预算估算与结构化会话压缩。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import TypeVar

from agent_forge.context.domain import ConversationHistoryDigest, ToolTransactionDigest
from agent_forge.context.application.text_budget import truncate_middle
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
class PromptWindowResult:
    """一次预算决策产生的最终消息和可审计度量。

    ``llm_messages`` 是最终模型输入；``conversation_history_digest`` 是可选的
    旧对话投影；``compacted`` 和 ``reason`` 解释是否压缩；其余计数字段记录
    覆盖消息数、压缩前后估算与硬上限。
    """

    llm_messages: list[Message]
    conversation_history_digest: ConversationHistoryDigest | None
    compacted: bool
    covered_message_count: int
    compacted_message_cursor: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    hard_input_limit: int
    reason: str


# 核心数据：完整模型请求进入窗口治理前的输入快照。
@dataclass(frozen=True, kw_only=True)
class PromptWindowRequest:
    """System、当前 Session 历史、旧投影、工具和强制压缩信号。"""

    turn_system_message: Message
    conversation_history: list[Message]
    observations: list[Observation]
    tool_schemas: list[ToolSchema]
    task: str
    previous_digest: ConversationHistoryDigest | None = None
    compacted_message_cursor: int = 0
    transient_messages: tuple[Message, ...] = ()
    force_compaction: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.compacted_message_cursor <= len(self.conversation_history):
            raise ValueError("compacted_message_cursor is outside current session history")
        if self.previous_digest is None and self.compacted_message_cursor:
            raise ValueError("a compaction cursor requires a previous digest")


@dataclass(frozen=True)
class _HistorySegment:
    """不可拆分的历史单元；工具意图和结果必须留在同一段。"""

    messages: list[Message]
    observations: list[Observation | None]


class PromptWindowManager:
    """在 LLM 边界前治理完整请求，不修改 live session 的原始会话历史。

    Trace 只记录窗口度量、来源 hash 与压缩投影事实，不保存完整 Prompt 正文。
    """

    def __init__(self, budget: PromptBudget) -> None:
        self.budget = budget

    # 主要入口：复用旧投影，只把当前 Session 尚未覆盖的前缀增量合入摘要。
    def prepare(self, request: PromptWindowRequest) -> PromptWindowResult:
        """在模型调用前生成满足硬窗口限制的、可解释的消息视图。

        规范上游是 ``TurnPreparation``；下一 owner 是 ``ModelPort``。返回值同时
        携带压缩前后 token 估算、覆盖范围和原因，供 trace 形成上下文证据。系统
        不变量是 system message 必须保留，assistant/tool 事务不得拆分，无法安全
        压缩时不得伪称已经满足 hard limit。
        """

        # region 1. 增量基线：旧摘要 + 当前 Session 未覆盖 raw tail
        # cursor 只索引当前 Session；resume 恢复旧 digest 后 cursor 从 0 重新开始。
        # transient budget message 永远留在 raw tail，不能进入摘要并污染 cursor。
        current_session_delta = request.conversation_history[
            request.compacted_message_cursor :
        ]
        previous_digest_message = (
            [Message(role="system", content=request.previous_digest.render())]
            if request.previous_digest is not None
            else []
        )
        full_llm_messages = [
            request.turn_system_message,
            *previous_digest_message,
            *current_session_delta,
            *request.transient_messages,
        ]
        estimated_tokens_before = estimate_prompt_tokens(
            full_llm_messages,
            request.tool_schemas,
            self.budget,
        )
        if (
            estimated_tokens_before <= self.budget.soft_input_limit
            and not request.force_compaction
        ):
            return PromptWindowResult(
                llm_messages=full_llm_messages,
                conversation_history_digest=request.previous_digest,
                compacted=request.previous_digest is not None,
                covered_message_count=(
                    request.previous_digest.covered_message_count
                    if request.previous_digest is not None
                    else 0
                ),
                compacted_message_cursor=request.compacted_message_cursor,
                estimated_tokens_before=estimated_tokens_before,
                estimated_tokens_after=estimated_tokens_before,
                hard_input_limit=self.budget.hard_input_limit,
                reason=(
                    "reused_previous_digest"
                    if request.previous_digest is not None
                    else "within_soft_limit"
                ),
            )
        # endregion 1. 增量基线结束

        # region 2. 安全切分：assistant ToolCall 与对应 tool Observation 不可拆散
        # 历史先按协议事务分组，再选择摘要边界；少于两个 segment 时没有“旧历史”
        # 可以安全替换，所以宁可报告无法压缩，也不拆坏 ToolCall 对应关系。
        observation_offset = sum(
            message.role == "tool"
            for message in request.conversation_history[
                : request.compacted_message_cursor
            ]
        )
        history_segments = _group_history_segments(
            current_session_delta,
            request.observations[observation_offset:],
        )
        if len(history_segments) < 2:
            return PromptWindowResult(
                llm_messages=full_llm_messages,
                conversation_history_digest=request.previous_digest,
                compacted=request.previous_digest is not None,
                covered_message_count=(
                    request.previous_digest.covered_message_count
                    if request.previous_digest is not None
                    else 0
                ),
                compacted_message_cursor=request.compacted_message_cursor,
                estimated_tokens_before=estimated_tokens_before,
                estimated_tokens_after=estimated_tokens_before,
                hard_input_limit=self.budget.hard_input_limit,
                reason=(
                    "insufficient_delta_to_compact"
                    if request.previous_digest is not None
                    else "insufficient_history_to_compact"
                ),
            )
        # endregion 2. 安全切分结束

        # region 3. Rolling 候选搜索：每个新 segment 只提取一次
        # cut_index 只扫描未覆盖 delta；已被 previous_digest 覆盖的 raw prefix 不再读取。
        # rolling_delta_digest 每轮只合入下一个 segment，避免 candidate 1 提取 81、
        # candidate 2 又提取 81~83。候选仍从最小 legal prefix 开始，首次满足预算即提交。
        target_token_count = (
            max(256, int(self.budget.soft_input_limit * 0.65))
            if request.force_compaction
            else self.budget.soft_input_limit
        )
        best_compaction_result: PromptWindowResult | None = None
        rolling_delta_digest: ConversationHistoryDigest | None = None
        root_user_message_pending = request.previous_digest is None
        for cut_index in range(1, len(history_segments)):
            next_segment = history_segments[cut_index - 1]
            segment_digest = _build_digest(
                (
                    request.previous_digest.task
                    if request.previous_digest is not None
                    else request.task
                ),
                [next_segment],
                estimated_tokens_before=estimated_tokens_before,
                skip_initial_user_message=root_user_message_pending,
            )
            if root_user_message_pending and any(
                message.role == "user" for message in next_segment.messages
            ):
                root_user_message_pending = False
            rolling_delta_digest = _merge_digest(
                rolling_delta_digest,
                segment_digest,
                estimated_tokens_before=estimated_tokens_before,
            )
            recent_messages = _flatten(history_segments[cut_index:])
            recent_messages = _trim_large_messages(
                recent_messages,
                max_chars=800 if request.force_compaction else 2_000,
            )
            conversation_history_digest = _merge_digest(
                request.previous_digest,
                rolling_delta_digest,
                estimated_tokens_before=estimated_tokens_before,
            )
            candidate_llm_messages = [
                request.turn_system_message,
                Message(
                    role="system",
                    content=conversation_history_digest.render(),
                ),
                *recent_messages,
                *request.transient_messages,
            ]
            estimated_tokens_after = estimate_prompt_tokens(
                candidate_llm_messages,
                request.tool_schemas,
                self.budget,
            )
            conversation_history_digest = replace(
                conversation_history_digest,
                estimated_tokens_after=estimated_tokens_after,
            )
            candidate_llm_messages[1] = Message(
                role="system",
                content=conversation_history_digest.render(),
            )
            estimated_tokens_after = estimate_prompt_tokens(
                candidate_llm_messages,
                request.tool_schemas,
                self.budget,
            )
            candidate_window_result = PromptWindowResult(
                llm_messages=candidate_llm_messages,
                conversation_history_digest=replace(
                    conversation_history_digest,
                    estimated_tokens_after=estimated_tokens_after,
                ),
                compacted=True,
                covered_message_count=(
                    conversation_history_digest.covered_message_count
                ),
                compacted_message_cursor=(
                    request.compacted_message_cursor
                    + rolling_delta_digest.covered_message_count
                ),
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
        # endregion 3. Rolling 候选搜索结束

        # region 4. 保守回退：只接受真实缩小的结果，否则明确报告无法安全压缩
        if (
            best_compaction_result is not None
            and best_compaction_result.estimated_tokens_after < estimated_tokens_before
        ):
            return best_compaction_result
        return PromptWindowResult(
            llm_messages=full_llm_messages,
            conversation_history_digest=request.previous_digest,
            compacted=request.previous_digest is not None,
            covered_message_count=(
                request.previous_digest.covered_message_count
                if request.previous_digest is not None
                else 0
            ),
            compacted_message_cursor=request.compacted_message_cursor,
            estimated_tokens_before=estimated_tokens_before,
            estimated_tokens_after=estimated_tokens_before,
            hard_input_limit=self.budget.hard_input_limit,
            reason=(
                "no_safe_incremental_boundary"
                if request.previous_digest is not None
                else "no_safe_compaction_boundary"
            ),
        )
        # endregion 4. 保守回退结束


def estimate_prompt_tokens(
    llm_messages: list[Message],
    tool_schemas: list[ToolSchema],
    budget: PromptBudget,
) -> int:
    """用统一近似估算完整请求；provider usage 仍是事后权威值。"""

    chars = 0
    for message in llm_messages:
        chars += len(message.role) + len(message.content or "")
        chars += len(message.name or "") + len(message.tool_call_id or "")
        chars += len(message.reasoning_content or "")
        chars += len(json.dumps(message.tool_calls or [], ensure_ascii=False))
        chars += 16
    chars += sum(
        len(json.dumps(tool_schema, ensure_ascii=False)) + 24
        for tool_schema in tool_schemas
    )
    return max(1, int(chars / max(1.0, budget.chars_per_token)))


def _group_history_segments(
    messages: list[Message],
    observations: list[Observation],
) -> list[_HistorySegment]:
    """把一轮 assistant tool intent 与随后的 tool 结果绑定成不可拆分单元。"""

    # Message 与 Observation 使用各自游标：前者保持协议原顺序，后者只在遇到
    # tool message 时前进，避免普通 user/assistant 消息打乱结果对应关系。
    observation_index = 0
    grouped_history_segments: list[_HistorySegment] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        segment_messages = [message]
        segment_observations: list[Observation | None] = [None]
        index += 1
        if message.role == "assistant" and message.tool_calls:
            # assistant ToolCall 与紧随其后的全部 tool message 组成一个事务；压缩时
            # 要么整段进入摘要，要么整段保留原文，绝不能只留下意图或只留下结果。
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
            # 历史可能从 tool message 开始（例如恢复或旧数据）；仍按顺序绑定可用的
            # Observation，但不伪造一个不存在的 assistant ToolCall。
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
    skip_initial_user_message: bool,
) -> ConversationHistoryDigest:
    """把被压缩的旧历史投影为可审计、大小有界的会话摘要。

    伪代码：冻结覆盖范围与来源 hash -> 提取初始任务后的 user 更新 -> 重建
    ToolCall/Observation 事务与失败证据 -> 返回有界 ``ConversationHistoryDigest``。
    原始消息仍保留在当前 live session；Trace 只记录窗口指标、来源 hash 和压缩事实，
    这里不修改权威会话历史。
    """

    # region 1. 来源边界：冻结覆盖消息、配对 Observation 与稳定 hash
    covered_messages = [
        message
        for history_segment in history_segments
        for message in history_segment.messages
    ]
    digest_source_payload = json.dumps(
        [
            {
                "message": {
                    "role": message.role,
                    "content": message.content,
                    "name": message.name,
                    "tool_call_id": message.tool_call_id,
                    "tool_calls": message.tool_calls,
                    "reasoning_content": message.reasoning_content,
                },
                "observation": (
                    {
                        "tool_name": observation.tool_name,
                        "success": observation.success,
                        "content": observation.content,
                        "execution_succeeded": observation.execution_succeeded,
                    }
                    if observation is not None
                    else None
                ),
            }
            for history_segment in history_segments
            for message, observation in zip(
                history_segment.messages,
                history_segment.observations,
            )
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    # endregion 1. 来源边界结束

    # region 2. 任务演进：保留初始任务之后的 user steer/约束更新
    # ``task`` 单独保留根任务；covered messages 中第一条 user message 只是原任务，
    # 后续 user message 才属于 task_updates，避免摘要重复发送同一语义。
    initial_user_message_seen = not skip_initial_user_message
    task_updates: list[str] = []
    for message in covered_messages:
        if message.role != "user":
            continue
        if not initial_user_message_seen:
            initial_user_message_seen = True
            continue
        task_updates.append(_excerpt(message.content, 320))
    task_updates = _bounded(
        [update for update in task_updates if update],
        6,
    )
    # endregion 2. 任务演进结束

    # region 3. 工具事务：按 call id 重建意图、结果和失败证据
    # 每个 segment 先索引实际 tool message，再遍历 assistant ToolCall；这样缺失结果时
    # success 保持 None，而不会把“模型提出调用”误写成“工具已经成功”。
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
    # endregion 3. 工具事务结束

    return ConversationHistoryDigest(
        task=task,
        covered_message_count=len(covered_messages),
        source_hash=hashlib.sha256(digest_source_payload.encode("utf-8")).hexdigest(),
        task_updates=task_updates,
        tool_transactions=_bounded(tool_transactions, 16),
        failed_tool_evidence=_bounded(failed_tool_evidence, 8),
        estimated_tokens_before=estimated_tokens_before,
        estimated_tokens_after=0,
    )


def _merge_digest(
    previous_digest: ConversationHistoryDigest | None,
    delta_digest: ConversationHistoryDigest,
    *,
    estimated_tokens_before: int,
) -> ConversationHistoryDigest:
    """把尚未覆盖的 raw delta 合入旧投影，不重新读取已覆盖消息。

    ``covered_message_count`` 是跨 Run 累计审计计数；真正索引当前 Session list 的
    cursor 由 ``AgentRunSession`` 单独拥有。source hash 使用前一投影 hash 与本次
    delta hash 形成确定性链，证明增量来源而不冒充完整 raw-conversation hash。
    """

    if previous_digest is None:
        return delta_digest
    chained_source_hash = hashlib.sha256(
        json.dumps(
            {
                "previous_digest": previous_digest.source_hash,
                "raw_delta": delta_digest.source_hash,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return ConversationHistoryDigest(
        task=previous_digest.task,
        covered_message_count=(
            previous_digest.covered_message_count
            + delta_digest.covered_message_count
        ),
        source_hash=chained_source_hash,
        task_updates=_bounded(
            [*previous_digest.task_updates, *delta_digest.task_updates],
            6,
        ),
        tool_transactions=_bounded(
            [
                *previous_digest.tool_transactions,
                *delta_digest.tool_transactions,
            ],
            16,
        ),
        failed_tool_evidence=_bounded(
            [
                *previous_digest.failed_tool_evidence,
                *delta_digest.failed_tool_evidence,
            ],
            8,
        ),
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
