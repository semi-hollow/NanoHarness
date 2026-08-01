"""长期记忆的候选、晋升、退役和召回用例。"""

from __future__ import annotations

import math
import re
import time
import uuid

from agent_forge.context.domain import (
    EvidenceReference,
    LongTermMemoryRecord,
    MemoryKind,
    MemoryProposal,
    MemoryStatus,
)
from agent_forge.context.ports import LongTermMemoryRepository


class LongTermMemoryService:
    """长期记忆生命周期用例。

    主链路是 ``propose -> promote -> recall``；``retire`` 和 ``reject`` 处理失效
    记录。Repository 只负责存取；本类决定哪些记录能进入模型上下文。
    """

    def __init__(self, repository: LongTermMemoryRepository) -> None:
        self._repository = repository

    # 主要入口：创建低权威候选，不自动进入模型上下文。
    def propose(self, proposal: MemoryProposal) -> LongTermMemoryRecord:
        """保存 candidate；只有显式 promote 后才允许召回。"""

        candidate_memory = LongTermMemoryRecord(
            memory_id=uuid.uuid4().hex,
            namespace=proposal.namespace,
            key=proposal.key.strip(),
            kind=proposal.kind,
            content=proposal.content.strip(),
            scope=proposal.scope,
            status=MemoryStatus.CANDIDATE.value,
            confidence=proposal.confidence,
            importance=proposal.importance,
            agent_name=proposal.agent_name,
            tags=list(proposal.tags),
            expires_at=proposal.expires_at,
        )
        candidate_memory.validate()
        self._repository.save(candidate_memory)
        return candidate_memory

    # 主要入口：证据通过后晋升，并退役相同 key 的旧真相。
    def promote(
        self,
        memory_id: str,
        evidence_refs: list[EvidenceReference],
    ) -> LongTermMemoryRecord:
        """将候选变为 active，并保留 supersede 链。"""

        # region 1. 晋升资格：只有候选/活跃记录且具备证据才能成为长期真相
        memory_to_promote = self._require(memory_id)
        if memory_to_promote.status not in {
            MemoryStatus.CANDIDATE.value,
            MemoryStatus.ACTIVE.value,
        }:
            raise ValueError(
                f"memory {memory_id} cannot be promoted from {memory_to_promote.status}"
            )
        merged_evidence_refs = _merge_evidence(
            memory_to_promote.evidence_refs,
            evidence_refs,
        )
        if not merged_evidence_refs:
            raise ValueError("promoting long-term memory requires evidence")
        # endregion 1. 晋升资格结束

        # region 2. 单一活跃版本：相同 namespace/key/scope 的旧真相先退役
        previous_active_memories = [
            existing_memory
            for existing_memory in self._repository.list_records(
                memory_to_promote.namespace
            )
            if existing_memory.memory_id != memory_to_promote.memory_id
            and existing_memory.key == memory_to_promote.key
            and existing_memory.scope == memory_to_promote.scope
            and existing_memory.agent_name == memory_to_promote.agent_name
            and existing_memory.status == MemoryStatus.ACTIVE.value
        ]
        promotion_timestamp = time.time()
        for previous_active_memory in previous_active_memories:
            previous_active_memory.status = MemoryStatus.SUPERSEDED.value
            previous_active_memory.updated_at = promotion_timestamp
            self._repository.save(previous_active_memory)
        # endregion 2. 单一活跃版本结束

        # region 3. 新真相落盘：保留证据和 supersede 链供审计
        memory_to_promote.evidence_refs = merged_evidence_refs
        memory_to_promote.status = MemoryStatus.ACTIVE.value
        memory_to_promote.updated_at = promotion_timestamp
        if previous_active_memories:
            memory_to_promote.supersedes = previous_active_memories[0].memory_id
        memory_to_promote.validate()
        self._repository.save(memory_to_promote)
        return memory_to_promote
        # endregion 3. 新真相落盘结束

    # 主要入口：按任务相关性召回，不返回候选、过期或越界记录。
    def recall(
        self,
        query: str,
        *,
        namespace: str,
        agent_name: str,
        limit: int = 6,
    ) -> list[LongTermMemoryRecord]:
        """组合词项相关度、置信度和重要度进行透明排序。"""

        # region 1. 可见性过滤：候选、过期、越 namespace/agent 的记录不参与排序
        query_terms = _terms(query)
        scored_memories: list[tuple[float, LongTermMemoryRecord]] = []
        for memory_record in self._repository.list_records(namespace):
            if not memory_record.visible_to(namespace, agent_name):
                continue
            # endregion 1. 可见性过滤结束

            # region 2. 可解释评分：相关度为主，置信度和重要度只做辅助
            memory_terms = _terms(
                " ".join(
                    [memory_record.key, memory_record.content, *memory_record.tags]
                )
            )
            overlapping_term_count = len(query_terms & memory_terms)
            relevance = overlapping_term_count / math.sqrt(
                max(1, len(query_terms)) * max(1, len(memory_terms))
            )
            always_relevant = (
                memory_record.kind
                in {MemoryKind.CONSTRAINT.value, MemoryKind.PREFERENCE.value}
                and memory_record.importance >= 0.8
            )
            if relevance <= 0 and not always_relevant:
                continue
            recall_score = (
                relevance * 0.65
                + memory_record.confidence * 0.2
                + memory_record.importance * 0.15
                + (0.1 if always_relevant else 0.0)
            )
            scored_memories.append((recall_score, memory_record))
            # endregion 2. 可解释评分结束

        # region 3. 稳定排序：分数、更新时间、memory_id 共同保证可复现结果
        scored_memories.sort(
            key=lambda scored_memory: (
                -scored_memory[0],
                -scored_memory[1].updated_at,
                scored_memory[1].memory_id,
            )
        )
        return [memory_record for _, memory_record in scored_memories[: max(0, limit)]]
        # endregion 3. 稳定排序结束

    # 主要入口：将已不适用的 active 记录退役，使后续召回不可见。
    def retire(self, memory_id: str) -> LongTermMemoryRecord:
        """显式退役不再可信或不再适用的记忆。"""

        memory_to_retire = self._require(memory_id)
        memory_to_retire.status = MemoryStatus.RETIRED.value
        memory_to_retire.updated_at = time.time()
        self._repository.save(memory_to_retire)
        return memory_to_retire

    # 主要入口：拒绝错误候选并保留审计记录，不物理删除历史。
    def reject(self, memory_id: str) -> LongTermMemoryRecord:
        """拒绝错误候选，保留审计事实。"""

        memory_to_reject = self._require(memory_id)
        memory_to_reject.status = MemoryStatus.REJECTED.value
        memory_to_reject.updated_at = time.time()
        self._repository.save(memory_to_reject)
        return memory_to_reject

    def _require(self, memory_id: str) -> LongTermMemoryRecord:
        memory_record = self._repository.get(memory_id)
        if memory_record is None:
            raise ValueError(f"memory not found: {memory_id}")
        return memory_record


def _merge_evidence(
    existing_evidence_refs: list[EvidenceReference],
    incoming_evidence_refs: list[EvidenceReference],
) -> list[EvidenceReference]:
    evidence_by_identity: dict[
        tuple[str, str, str, str],
        EvidenceReference,
    ] = {}
    for evidence_ref in [*existing_evidence_refs, *incoming_evidence_refs]:
        evidence_identity = (
            evidence_ref.source_type,
            evidence_ref.source_id,
            evidence_ref.path,
            evidence_ref.sha256,
        )
        evidence_by_identity[evidence_identity] = evidence_ref
    return list(evidence_by_identity.values())


def _terms(text: str) -> set[str]:
    """同时生成英文词项、中文单字和中文双字词项。"""

    normalized_memory_text = text.lower()
    normalized_terms = set(re.findall(r"[a-z0-9_]+", normalized_memory_text))
    chinese_characters = re.findall(r"[\u4e00-\u9fff]", normalized_memory_text)
    normalized_terms.update(chinese_characters)
    normalized_terms.update(
        "".join(chinese_characters[index : index + 2])
        for index in range(len(chinese_characters) - 1)
    )
    return {term for term in normalized_terms if term}
