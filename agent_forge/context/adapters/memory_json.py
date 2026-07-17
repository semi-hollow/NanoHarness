"""每条记录独立文件的长期记忆 JSON Repository。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from agent_forge.context.domain import LongTermMemoryRecord


class JsonLongTermMemoryRepository:
    """以 namespace 分目录持久化，避免不同项目共享同一文件。"""

    def __init__(self, root: str | Path = ".agent_forge/memory") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # 运行时端口：校验并原子保存领域记录，不改变其权威状态。
    def save(self, record: LongTermMemoryRecord) -> None:
        """校验后使用临时文件和原子替换写入。"""

        record.validate()
        path = self._path_for(record.namespace, record.memory_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    # 运行时端口：按稳定 ID 读取记录，供生命周期用例修改或审计。
    def get(self, memory_id: str) -> LongTermMemoryRecord | None:
        """在隔离目录中查找稳定 ID；记录规模刻意保持轻量。"""

        self._validate_memory_id(memory_id)
        for path in self.root.glob(f"*/{memory_id}.json"):
            return self._load(path)
        return None

    # 运行时端口：列出候选记录；可见性过滤仍由 LongTermMemoryService 负责。
    def list_records(self, namespace: str | None = None) -> list[LongTermMemoryRecord]:
        """跳过损坏文件，并按更新时间倒序返回。"""

        pattern = f"{self._namespace_key(namespace)}/*.json" if namespace else "*/*.json"
        records: list[LongTermMemoryRecord] = []
        for path in self.root.glob(pattern):
            try:
                records.append(self._load(path))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return sorted(records, key=lambda item: item.updated_at, reverse=True)

    def _path_for(self, namespace: str, memory_id: str) -> Path:
        self._validate_memory_id(memory_id)
        return self.root / self._namespace_key(namespace) / f"{memory_id}.json"

    @staticmethod
    def _namespace_key(namespace: str | None) -> str:
        value = namespace or ""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def _validate_memory_id(memory_id: str) -> None:
        if not memory_id or Path(memory_id).name != memory_id:
            raise ValueError("memory_id must be one safe path segment")

    @staticmethod
    def _load(path: Path) -> LongTermMemoryRecord:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"memory record must be an object: {path}")
        return LongTermMemoryRecord.from_dict(data)
