"""用户显式控制的长期记忆用例。

这里故意只保留 ``remember / forget / list / recall`` 四个动作。
模型不能自行候选、打分或晋升跨 Run 记忆，避免错误结论污染后续任务。
"""

from __future__ import annotations

import time
import uuid

from agent_forge.context.domain import (
    LongTermMemoryRecord,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    USER_MEMORY_NAMESPACE,
)
from agent_forge.context.ports import (
    LongTermMemoryRecallPort,
    LongTermMemoryRepository,
)


class LongTermMemoryService(LongTermMemoryRecallPort):
    """长期记忆的唯一应用服务。

    可以类比 Java 中的 Application Service：Repository 只负责 JSON 存取，
    本类负责作用域、同 key 更新和项目值覆盖用户默认值等业务规则。
    """

    def __init__(self, repository: LongTermMemoryRepository) -> None:
        self._repository = repository

    # 主要入口：用户显式新增或更新一条跨 Run 记忆。
    def remember(
        self,
        *,
        project_namespace: str,
        key: str,
        content: str,
        scope: str,
    ) -> LongTermMemoryRecord:
        """同作用域、同 key 原地更新，保留 ID 并递增 revision。"""

        # region 1. 校验用户命令
        normalized_key = key.strip()
        normalized_content = content.strip()
        if not normalized_key or not normalized_content:
            raise ValueError("memory key and content are required")
        if scope not in {memory_scope.value for memory_scope in MemoryScope}:
            raise ValueError(f"unsupported memory scope: {scope}")
        # endregion 1. 校验用户命令结束

        # region 2. 定位同作用域、同 key 的已有记忆
        storage_namespace = self._storage_namespace(
            project_namespace=project_namespace,
            scope=scope,
        )
        existing_record = self._find_by_key(
            namespace=storage_namespace,
            scope=scope,
            key=normalized_key,
        )
        # endregion 2. 定位同作用域、同 key 的已有记忆结束

        # region 3. 更新原记录，或创建第一版记录
        # 同 key 更新保留 memory_id，便于外部引用稳定；新 key 才创建新 ID。
        # 两条路径都先通过领域 validate，再交给 Repository 原子持久化。
        now = time.time()
        if existing_record is not None:
            existing_record.key = normalized_key
            existing_record.content = normalized_content
            existing_record.revision += 1
            existing_record.updated_at = now
            existing_record.validate()
            self._repository.save(existing_record)
            return existing_record

        new_record = LongTermMemoryRecord(
            memory_id=uuid.uuid4().hex,
            namespace=storage_namespace,
            key=normalized_key,
            content=normalized_content,
            scope=scope,
            source=MemorySource.USER_EXPLICIT.value,
            status=MemoryStatus.ACTIVE.value,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        new_record.validate()
        self._repository.save(new_record)
        return new_record
        # endregion 3. 更新原记录，或创建第一版记录结束

    # 主要入口：用户显式忘记一条记忆。
    def forget(self, memory_id: str) -> LongTermMemoryRecord:
        """物理删除记录；已启动 Run 的快照不受影响。"""

        # region 1. 确认目标存在
        memory_record = self._require(memory_id)
        # endregion 1. 确认目标存在结束

        # region 2. 删除并返回被删除的事实
        self._repository.delete(memory_id)
        return memory_record
        # endregion 2. 删除并返回被删除的事实结束

    # 主要入口：列出用户全局记忆和当前项目记忆。
    def list_for_project(
        self,
        *,
        project_namespace: str,
        scope: str | None = None,
    ) -> list[LongTermMemoryRecord]:
        """返回可管理的原始记录，不合并同 key 覆盖关系。"""

        # region 1. 校验作用域并加载用户级、项目级记录
        if scope is not None and scope not in {
            memory_scope.value for memory_scope in MemoryScope
        }:
            raise ValueError(f"unsupported memory scope: {scope}")
        stored_memories = [
            *self._repository.list_records(USER_MEMORY_NAMESPACE),
            *self._repository.list_records(project_namespace),
        ]
        # endregion 1. 校验作用域并加载用户级、项目级记录结束

        # region 2. 过滤当前项目可见记录并稳定排序
        # list 是管理视图，不做同 key 覆盖；用户需要同时看见全局默认值和项目值，
        # 真正注入 Runtime 时才由 recall 执行项目级覆盖规则。
        visible_records = [
            memory_record
            for memory_record in stored_memories
            if memory_record.visible_to(project_namespace)
            and (scope is None or memory_record.scope == scope)
        ]
        return sorted(
            visible_records,
            key=lambda memory_record: (
                0 if memory_record.scope == MemoryScope.USER.value else 1,
                memory_record.key.casefold(),
                memory_record.memory_id,
            ),
        )
        # endregion 2. 过滤当前项目可见记录并稳定排序结束

    # Runtime 入口：在 Run 开始时生成一份固定记忆快照。
    def recall(
        self,
        *,
        namespace: str,
        max_chars: int = 2_000,
    ) -> list[LongTermMemoryRecord]:
        """按字符预算选择完整记录；项目同 key 覆盖用户默认值。"""

        # region 1. 在 Run 开始时加载当前可见记录
        if max_chars <= 0:
            return []
        visible_records = self.list_for_project(project_namespace=namespace)
        # endregion 1. 在 Run 开始时加载当前可见记录结束

        # region 2. 同 key 冲突时让项目记忆覆盖用户默认值
        selected_by_key: dict[str, LongTermMemoryRecord] = {}
        for memory_record in visible_records:
            normalized_key = memory_record.key.casefold()
            current_selected_memory = selected_by_key.get(normalized_key)
            if (
                current_selected_memory is None
                or memory_record.scope == MemoryScope.PROJECT.value
            ):
                selected_by_key[normalized_key] = memory_record
        # endregion 2. 同 key 冲突时让项目记忆覆盖用户默认值结束

        # region 3. 项目优先、更新时间倒序；整条记录装得下才进入快照
        ranked_records = sorted(
            selected_by_key.values(),
            key=lambda memory_record: (
                0 if memory_record.scope == MemoryScope.PROJECT.value else 1,
                -memory_record.updated_at,
                memory_record.key.casefold(),
                memory_record.memory_id,
            ),
        )
        selected_records: list[LongTermMemoryRecord] = []
        used_chars = 0
        for memory_record in ranked_records:
            separator_chars = 1 if selected_records else 0
            record_chars = len(memory_record.render_prompt_line())
            if used_chars + separator_chars + record_chars > max_chars:
                continue
            selected_records.append(memory_record)
            used_chars += separator_chars + record_chars
        return selected_records
        # endregion 3. 稳定排序并冻结本次 Run 的记忆上限结束

    def _find_by_key(
        self,
        *,
        namespace: str,
        scope: str,
        key: str,
    ) -> LongTermMemoryRecord | None:
        normalized_key = key.casefold()
        return next(
            (
                memory_record
                for memory_record in self._repository.list_records(namespace)
                if memory_record.scope == scope
                and memory_record.key.casefold() == normalized_key
            ),
            None,
        )

    def _require(self, memory_id: str) -> LongTermMemoryRecord:
        memory_record = self._repository.get(memory_id)
        if memory_record is None:
            raise ValueError(f"memory not found: {memory_id}")
        return memory_record

    @staticmethod
    def _storage_namespace(*, project_namespace: str, scope: str) -> str:
        if scope == MemoryScope.USER.value:
            return USER_MEMORY_NAMESPACE
        normalized_project_namespace = project_namespace.strip()
        if not normalized_project_namespace:
            raise ValueError("project memory requires a project namespace")
        return normalized_project_namespace
