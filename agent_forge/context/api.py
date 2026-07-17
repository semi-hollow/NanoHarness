"""外围入口使用的 Context capability 公共 API。"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from agent_forge.context.adapters import JsonLongTermMemoryRepository
from agent_forge.context.application import LongTermMemoryService
from agent_forge.context.domain import (
    EvidenceReference,
    LongTermMemoryRecord,
    MemoryProposal,
)


# 核心数据：外围创建长期记忆候选时提交的用户输入。
@dataclass(frozen=True)
class ProposeMemoryRequest:
    """仓储位置、workspace 默认值、候选知识和 TTL。"""

    memory_root: str
    workspace: str
    namespace: str
    key: str
    kind: str
    content: str
    scope: str
    agent_name: str = ""
    confidence: float = 0.5
    importance: float = 0.5
    tags: tuple[str, ...] = ()
    ttl_seconds: float | None = None


# 主要入口：从 CLI/UI 创建 candidate；该记录尚不能进入 Agent 上下文。
def propose_memory(request: ProposeMemoryRequest) -> LongTermMemoryRecord:
    """创建不会自动进入上下文的低权威候选。"""

    expires_at = (
        time.time() + request.ttl_seconds if request.ttl_seconds is not None else None
    )
    proposal = MemoryProposal(
        namespace=request.namespace.strip() or str(Path(request.workspace).resolve()),
        key=request.key,
        kind=request.kind,
        content=request.content,
        scope=request.scope,
        agent_name=request.agent_name,
        confidence=request.confidence,
        importance=request.importance,
        tags=request.tags,
        expires_at=expires_at,
    )
    return _service(request.memory_root).propose(proposal)


# 主要入口：绑定证据并把 candidate 晋升为可召回的 active 记录。
def promote_memory(
    memory_root: str,
    memory_id: str,
    evidence_refs: list[EvidenceReference],
) -> LongTermMemoryRecord:
    """以显式证据晋升一条长期记忆。"""

    return _service(memory_root).promote(memory_id, evidence_refs)


def build_evidence_reference(value: str) -> EvidenceReference:
    """把操作员给出的文件或证据标识转换为可追溯引用。"""

    path = Path(value).expanduser()
    if path.is_file():
        resolved = path.resolve()
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        return EvidenceReference(
            source_type="local_file",
            source_id=str(resolved),
            path=str(resolved),
            sha256=digest,
        )
    return EvidenceReference(
        source_type="operator_reference",
        source_id=value.strip(),
    )


# 主要入口：让已失效的 active 记录退出后续召回。
def retire_memory(memory_root: str, memory_id: str) -> LongTermMemoryRecord:
    """退役已失效记忆。"""

    return _service(memory_root).retire(memory_id)


# 主要入口：拒绝错误 candidate，同时保留其审计历史。
def reject_memory(memory_root: str, memory_id: str) -> LongTermMemoryRecord:
    """拒绝错误候选。"""

    return _service(memory_root).reject(memory_id)


# 主要入口：列出长期记忆状态，供操作员审计而非模型直接使用。
def list_memories(
    memory_root: str,
    workspace: str | None = None,
    namespace: str = "",
) -> list[LongTermMemoryRecord]:
    """读取全部记录，或限制到指定 workspace。"""

    selected_namespace = namespace.strip() or (
        str(Path(workspace).resolve()) if workspace else None
    )
    return JsonLongTermMemoryRepository(memory_root).list_records(selected_namespace)


def _service(memory_root: str) -> LongTermMemoryService:
    return LongTermMemoryService(JsonLongTermMemoryRepository(memory_root))


__all__ = [
    "build_evidence_reference",
    "list_memories",
    "promote_memory",
    "ProposeMemoryRequest",
    "propose_memory",
    "reject_memory",
    "retire_memory",
]
