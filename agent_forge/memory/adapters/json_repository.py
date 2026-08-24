"""每条记录独立文件的 Long-Term Memory JSON Repository。

系统角色：按 namespace 隔离目录、按 ``memory_id`` 定位记录，并严格读写当前 canonical
schema；不决定 recall、scope override 或 consolidation。
输入：已通过 Domain 校验的 ``LongTermMemoryRecord``；输出：原子持久化/读取记录。
相邻边界：``LongTermMemoryService`` 拥有业务语义，本 Adapter 只拥有路径与存取。

折叠导航：1 save/get；2 list/delete；3 identity/path/load helper。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_forge.infrastructure.atomic_json import atomic_write_json
from agent_forge.memory.domain import LongTermMemoryRecord
from agent_forge.memory.ports import LongTermMemoryRepository
from agent_forge.infrastructure.storage_layout import MEMORY_ROOT


class JsonLongTermMemoryRepository(LongTermMemoryRepository):
    """以 namespace 分目录持久化，避免不同项目共享同一文件。"""

    def __init__(
        self,
        root: str | Path = MEMORY_ROOT,
    ) -> None:
        self.root = Path(root)

    # region 1. Save / Get：稳定 ID 跨 Run 定位同一记录
    # 运行时端口：校验并原子保存领域记录，不改变其权威状态。
    def save(self, record: LongTermMemoryRecord) -> None:
        """校验后使用临时文件和原子替换写入。"""

        record.validate()
        path = self._path_for(record.namespace, record.memory_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, record.to_dict())

    # 运行时端口：按稳定 ID 读取记录，供更新或删除用例使用。
    def get(self, memory_id: str) -> LongTermMemoryRecord | None:
        """在隔离目录中查找稳定 ID；记录规模刻意保持轻量。"""

        self._validate_memory_id(memory_id)
        for path in self.root.glob(f"*/{memory_id}.json"):
            return self._load(path)
        return None
    # endregion 1. Save / Get 结束

    # region 2. List / Delete：可见性和排序策略仍由 Application 负责
    # 运行时端口：严格读取当前 schema；可见性过滤由 LongTermMemoryService 负责。
    def list_records(self, namespace: str | None = None) -> list[LongTermMemoryRecord]:
        """按更新时间倒序返回；活动目录中的损坏或旧 schema 直接失败。"""

        pattern = f"{self._namespace_key(namespace)}/*.json" if namespace else "*/*.json"
        records = [self._load(path) for path in self.root.glob(pattern)]
        return sorted(records, key=lambda item: item.updated_at, reverse=True)

    # 运行时端口：用户显式 forget 时删除对应 JSON 文件。
    def delete(self, memory_id: str) -> None:
        """只删除唯一命中的记录；不影响已启动 Run 的内存快照。"""

        self._validate_memory_id(memory_id)
        matching_paths = list(self.root.glob(f"*/{memory_id}.json"))
        if not matching_paths:
            raise ValueError(f"memory not found: {memory_id}")
        matching_paths[0].unlink()
    # endregion 2. List / Delete 结束

# region 3. Namespace、路径与 schema 辅助逻辑
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
    # endregion 3. Namespace/path/schema helper 结束
