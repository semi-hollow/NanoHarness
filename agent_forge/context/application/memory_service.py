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
    MemoryScope,
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

        record = LongTermMemoryRecord(
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
        record.validate()
        self._repository.save(record)
        return record

    # 主要入口：证据通过后晋升，并退役相同 key 的旧真相。
    def promote(
        self,
        memory_id: str,
        evidence_refs: list[EvidenceReference],
    ) -> LongTermMemoryRecord:
        """将候选变为 active，并保留 supersede 链。"""

        record = self._require(memory_id)
        if record.status not in {
            MemoryStatus.CANDIDATE.value,
            MemoryStatus.ACTIVE.value,
        }:
            raise ValueError(
                f"memory {memory_id} cannot be promoted from {record.status}"
            )
        merged = _merge_evidence(record.evidence_refs, evidence_refs)
        if not merged:
            raise ValueError("promoting long-term memory requires evidence")

        previous = [
            item
            for item in self._repository.list_records(record.namespace)
            if item.memory_id != record.memory_id
            and item.key == record.key
            and item.scope == record.scope
            and item.agent_name == record.agent_name
            and item.status == MemoryStatus.ACTIVE.value
        ]
        now = time.time()
        for old in previous:
            old.status = MemoryStatus.SUPERSEDED.value
            old.updated_at = now
            self._repository.save(old)

        record.evidence_refs = merged
        record.status = MemoryStatus.ACTIVE.value
        record.updated_at = now
        if previous:
            record.supersedes = previous[0].memory_id
        record.validate()
        self._repository.save(record)
        return record

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

        query_terms = _terms(query)
        scored: list[tuple[float, LongTermMemoryRecord]] = []
        for record in self._repository.list_records(namespace):
            if not record.visible_to(namespace, agent_name):
                continue
            record_terms = _terms(" ".join([record.key, record.content, *record.tags]))
            overlap = len(query_terms & record_terms)
            relevance = overlap / math.sqrt(
                max(1, len(query_terms)) * max(1, len(record_terms))
            )
            always_relevant = (
                record.kind
                in {MemoryKind.CONSTRAINT.value, MemoryKind.PREFERENCE.value}
                and record.importance >= 0.8
            )
            if relevance <= 0 and not always_relevant:
                continue
            score = (
                relevance * 0.65
                + record.confidence * 0.2
                + record.importance * 0.15
                + (0.1 if always_relevant else 0.0)
            )
            scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], -item[1].updated_at, item[1].memory_id))
        return [record for _, record in scored[: max(0, limit)]]

    # 主要入口：将已不适用的 active 记录退役，使后续召回不可见。
    def retire(self, memory_id: str) -> LongTermMemoryRecord:
        """显式退役不再可信或不再适用的记忆。"""

        record = self._require(memory_id)
        record.status = MemoryStatus.RETIRED.value
        record.updated_at = time.time()
        self._repository.save(record)
        return record

    # 主要入口：拒绝错误候选并保留审计记录，不物理删除历史。
    def reject(self, memory_id: str) -> LongTermMemoryRecord:
        """拒绝错误候选，保留审计事实。"""

        record = self._require(memory_id)
        record.status = MemoryStatus.REJECTED.value
        record.updated_at = time.time()
        self._repository.save(record)
        return record

    def _require(self, memory_id: str) -> LongTermMemoryRecord:
        record = self._repository.get(memory_id)
        if record is None:
            raise ValueError(f"memory not found: {memory_id}")
        return record


def _merge_evidence(
    existing: list[EvidenceReference],
    incoming: list[EvidenceReference],
) -> list[EvidenceReference]:
    merged: dict[tuple[str, str, str, str], EvidenceReference] = {}
    for item in [*existing, *incoming]:
        merged[(item.source_type, item.source_id, item.path, item.sha256)] = item
    return list(merged.values())


def _terms(text: str) -> set[str]:
    """同时生成英文词项、中文单字和中文双字词项。"""

    lowered = text.lower()
    terms = set(re.findall(r"[a-z0-9_]+", lowered))
    chinese = re.findall(r"[\u4e00-\u9fff]", lowered)
    terms.update(chinese)
    terms.update(
        "".join(chinese[index : index + 2]) for index in range(len(chinese) - 1)
    )
    return {term for term in terms if term}
