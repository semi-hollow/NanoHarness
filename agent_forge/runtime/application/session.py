"""一次 Agent Run 的有界 Conversation 视图与显式可变状态。

系统角色：从 durable Thread journal 重建 provider-valid Conversation page，并用
``AgentRunSession`` 集中保存 AgentLoop 当前 attempt 的内存状态。
输入：权威 ``ConversationItem`` 与 Runtime dependencies；输出：规范化消息视图和
唯一 mutable session owner。
相邻边界：Thread Repository 拥有 raw truth；本文件只做读取投影与状态承载；
``AgentLoop`` / Application services 决定下一步控制流。

折叠导航：1 Conversation 事务页；2 provider 顺序投影；3 Run session state。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_forge.contracts import JsonObject, ToolSchema
from agent_forge.memory.domain import LongTermMemoryRecord
from agent_forge.runtime.application.working_memory import WorkingMemory
from agent_forge.observability.domain.evidence import EvidenceLedger
from agent_forge.runtime.application.step_control import StepController
from agent_forge.runtime.domain.conversation import Message, Observation
from agent_forge.runtime.domain.thread import ConversationItem
from agent_forge.runtime.application.run_lifecycle import RunLifecycle
from agent_forge.runtime.ports.skills import SkillView
from agent_forge.runtime.ports.thread import ConversationThreadRepository


# region 1. Conversation 事务页：有界读取，但不把 Assistant ToolCall batch 截断
def load_transaction_safe_conversation_page(
    repository: ConversationThreadRepository,
    *,
    thread_id: str,
    after_sequence: int,
    limit: int = 200,
    max_lookahead: int = 64,
) -> list[ConversationItem]:
    """有界读取一页，补齐事务并生成 provider-valid 投影。

    raw journal 为了人工授权 provenance，会先持久化 ``ask_human``
    回答、再追加对应 Tool Observation。模型输入不能照搬该顺序；
    本函数在不改写 journal 的前提下投影为
    ``assistant -> all tool results -> authoritative answer``。
    """

    items = repository.list_items(
        thread_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    if not items:
        return items

    # 只可能有最后一个事务跨页；最多多读一个小 lookahead。
    incomplete_index = _first_incomplete_transaction_index(items)
    if incomplete_index is not None:
        lookahead = repository.list_items(
            thread_id,
            after_sequence=items[-1].sequence,
            limit=max_lookahead,
        )
        # 只读到当前跨页事务闭合即停止，避免“补事务”变成无界读取历史。
        for candidate in lookahead:
            items.append(candidate)
            if _first_incomplete_transaction_index(items) is None:
                break
        if _first_incomplete_transaction_index(items) is not None:
            raise RuntimeError(
                "Conversation page ends inside an incomplete bounded tool transaction"
            )
    return _normalize_conversation_transactions(items)


def _first_incomplete_transaction_index(
    items: list[ConversationItem],
) -> int | None:
    """校验页内事务顺序；只允许 ask_human 授权回答穿过 raw batch。"""

    # 顺序扫描 Assistant batch；普通消息直接跳过，只有 tool_calls 打开一个事务。
    for index, item in enumerate(items):
        if item.role != "assistant" or not item.tool_calls:
            continue
        expected = {
            str(call.get("id") or "") for call in item.tool_calls
            if str(call.get("id") or "")
        }
        if len(expected) != len(item.tool_calls):
            raise RuntimeError(
                "Conversation journal assistant batch has missing or duplicate call ids"
            )
        ask_human_ids = {
            str(call.get("id") or "")
            for call in item.tool_calls
            if _tool_call_name(call) == "ask_human"
        }
        observed: set[str] = set()
        cursor = index + 1
        # batch 内只接受对应 Tool Observation；ask_human 的权威回答是唯一合法穿插项。
        while cursor < len(items) and observed != expected:
            candidate = items[cursor]
            if candidate.role == "tool":
                call_id = str(candidate.tool_call_id or "")
                if not call_id or call_id not in expected or call_id in observed:
                    raise RuntimeError(
                        "Conversation journal contains an invalid tool batch result"
                    )
                observed.add(call_id)
                cursor += 1
                continue
            if (
                ask_human_ids - observed
                and candidate.role == "user"
                and candidate.human_authority
                and candidate.origin in {"human", "operator"}
                and bool(candidate.metadata.get("human_input_request_id"))
            ):
                cursor += 1
                continue
            raise RuntimeError(
                "Conversation journal interleaves a tool batch with another item"
            )
        if observed != expected:
            return index
    return None
# endregion 1. Conversation 事务页结束


# region 2. Provider 顺序投影：不改 durable journal，只重排当前模型输入
def _normalize_conversation_transactions(
    items: list[ConversationItem],
) -> list[ConversationItem]:
    """raw authority 保持不变，只重排 ask_human 批次的模型输入视图。"""

    normalized: list[ConversationItem] = []
    index = 0
    # 每次循环复制一个普通 item，或一次完整 Assistant transaction。
    while index < len(items):
        assistant = items[index]
        if assistant.role != "assistant" or not assistant.tool_calls:
            normalized.append(assistant)
            index += 1
            continue
        expected = {
            str(call.get("id") or "") for call in assistant.tool_calls
            if str(call.get("id") or "")
        }
        transaction: list[ConversationItem] = [assistant]
        observed: set[str] = set()
        cursor = index + 1
        # 第一阶段收集完整 batch，第二阶段才按 provider 协议重排 tool 与 authority item。
        while cursor < len(items) and observed != expected:
            candidate = items[cursor]
            transaction.append(candidate)
            if candidate.role == "tool" and candidate.tool_call_id:
                observed.add(str(candidate.tool_call_id))
            cursor += 1
        tool_results = [item for item in transaction[1:] if item.role == "tool"]
        authority_items = [item for item in transaction[1:] if item.role != "tool"]
        normalized.extend([assistant, *tool_results, *authority_items])
        index = cursor
    return normalized


def _tool_call_name(call: JsonObject) -> str:
    function = call.get("function")
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return str(call.get("name") or "")
# endregion 2. Provider 顺序投影结束


# region 3. Run session state：AgentLoop 内唯一 mutable owner，不复制策略
# 核心数据：AgentLoop 内部唯一的可变 run 状态容器。
@dataclass
class AgentRunSession:
    """一次 ``AgentLoop.run`` 的全部可变状态。

    字段按职责分为：任务身份、生命周期依赖、消息与观察、
    上下文与证据、
    Skill 与
    工具历史、预算和最终状态。这里保存运行数据，不决定策略；控制流在
    ``AgentLoop``，工具治理在 ``ToolExecutionPipeline``，持久化在 ``RunLifecycle``。
    """

    # Thread/Turn 是 Conversation 身份；Run 只持有本 attempt 的有界视图。
    thread_id: str
    turn_id: str
    thread_initial_task: str
    root_task: str
    turn_focus: str
    turn_focus_item_id: str
    agent_name: str
    workspace_root: str
    max_iterations: int
    lifecycle: RunLifecycle
    controller: StepController
    conversation_threads: ConversationThreadRepository
    context_revision: int = 0
    # Conversation History：从权威 Thread journal 重建的当前有界模型视图。
    iteration: int = 0
    messages: list[Message] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    message_sequences: list[int] = field(default_factory=list)
    # Stable prefix / base schemas / reasoning memory 只在 Turn 首次 attempt 冻结。
    stable_system_prefix: str = ""
    base_tool_schemas: list[ToolSchema] = field(default_factory=list)
    stable_context_evidence: JsonObject = field(default_factory=dict)
    long_term_memory_snapshot: list[LongTermMemoryRecord] = field(default_factory=list)
    # Runtime Context 的动态输入；不镜像 raw Conversation/Tool Observation。
    working_memory: WorkingMemory = field(default_factory=WorkingMemory)
    # 与 Turn-level reasoning snapshot 分离；只随 human input / memory mutation 变化。
    memory_management_candidates: list[LongTermMemoryRecord] = field(default_factory=list)
    memory_management_candidates_key: str = ""
    evidence: EvidenceLedger = field(default_factory=EvidenceLedger)
    active_skills: list[SkillView] = field(default_factory=list)
    skill_tool_names: set[str] = field(default_factory=set)
    # 工具重复检测由 StepController 拥有；Session 只保存验证事实和资源累计。
    ran_tests: bool = False
    blocked: bool = False
    estimated_cost_usd: float = 0.0
    # 返回调用方的本次停止输出；accepted final answer 由 RunLifecycle 质量门决定。
    status: str = "running"
    stop_output: str = ""
    stop_reason: str = ""
# endregion 3. Run session state 结束
