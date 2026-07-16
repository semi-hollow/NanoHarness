"""外围入口使用的 Context capability 公共 API。"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from agent_forge.context.adapters import JsonLongTermMemoryRepository
from agent_forge.context.application import LongTermMemoryService
from agent_forge.context.domain import EvidenceReference, LongTermMemoryRecord


def propose_memory(
    *,
    memory_root: str,
    workspace: str,
    namespace: str = "",
    key: str,
    kind: str,
    content: str,
    scope: str,
    agent_name: str = "",
    confidence: float = 0.5,
    importance: float = 0.5,
    tags: list[str] | None = None,
    ttl_seconds: float | None = None,
) -> LongTermMemoryRecord:
    """创建不会自动进入上下文的低权威候选。"""

    expires_at = time.time() + ttl_seconds if ttl_seconds is not None else None
    return _service(memory_root).propose(
        namespace=namespace.strip() or str(Path(workspace).resolve()),
        key=key,
        kind=kind,
        content=content,
        scope=scope,
        agent_name=agent_name,
        confidence=confidence,
        importance=importance,
        tags=tags,
        expires_at=expires_at,
    )


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


def retire_memory(memory_root: str, memory_id: str) -> LongTermMemoryRecord:
    """退役已失效记忆。"""

    return _service(memory_root).retire(memory_id)


def reject_memory(memory_root: str, memory_id: str) -> LongTermMemoryRecord:
    """拒绝错误候选。"""

    return _service(memory_root).reject(memory_id)


def list_memories(
    memory_root: str,
    workspace: str | None = None,
    namespace: str = "",
) -> list[LongTermMemoryRecord]:
    """读取全部记录，或限制到指定 workspace。"""

    selected_namespace = (
        namespace.strip()
        or (str(Path(workspace).resolve()) if workspace else None)
    )
    return JsonLongTermMemoryRepository(memory_root).list_records(
        selected_namespace
    )


def _service(memory_root: str) -> LongTermMemoryService:
    return LongTermMemoryService(JsonLongTermMemoryRepository(memory_root))


__all__ = [
    "build_evidence_reference",
    "list_memories",
    "promote_memory",
    "propose_memory",
    "reject_memory",
    "retire_memory",
]
