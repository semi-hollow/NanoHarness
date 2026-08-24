"""用户显式控制的长期记忆用例。

这里集中 ``apply_consolidation / recall / management_candidates`` 与人工管理动作。
模型不能自行挖掘或晋升跨 Run 记忆，避免错误结论污染后续任务。

系统角色：唯一负责 Memory identity、scope override、typed consolidation、召回排序与预算；
Repository 只保存 JSON，Tool 只传递已经过 authority 校验的 proposal。
输入：显式用户管理动作或 Runtime recall request；输出：记录、Reasoning Snapshot 或
Management Candidates。
核心阅读：写入看 ``apply_consolidation``，读取看 ``recall``，当前 Turn 合并看
``management_candidates``。
"""

from __future__ import annotations

import re
import time
import uuid

from agent_forge.memory.domain import (
    LongTermMemoryRecord,
    MemoryConsolidationAction,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    USER_MEMORY_NAMESPACE,
)
from agent_forge.memory.ports import (
    LongTermMemoryRecallPort,
    LongTermMemoryRepository,
)


class LongTermMemoryService(LongTermMemoryRecallPort):
    """长期记忆的唯一应用服务。

    Repository 只负责 JSON 存取；本类负责 ID 更新、作用域、项目值覆盖用户默认值、
    task-aware recall 和有界 management candidates。
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
        source_quote: str = "",
    ) -> LongTermMemoryRecord:
        """供人工管理入口使用的 exact-key convenience upsert。

        记录 identity 仍是 ``memory_id``；这里按 key 定位只是 management API 的显式
        编辑便利。模型写路径必须调用 ``apply_consolidation`` 并提交 target ID。
        """

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

        # region 3. 把人工 upsert 转成同一套 ID-based consolidation
        return self.apply_consolidation(
            project_namespace=project_namespace,
            action=(
                MemoryConsolidationAction.UPDATE.value
                if existing_record is not None
                else MemoryConsolidationAction.CREATE.value
            ),
            target_memory_id=(
                existing_record.memory_id if existing_record is not None else ""
            ),
            key=normalized_key,
            content=normalized_content,
            scope=scope,
            source_quote=(source_quote.strip() or normalized_content),
        )
        # endregion 3. 更新原记录，或创建第一版记录结束

    # 模型写入口：只接受显式 action，UPDATE/NOOP 必须绑定稳定 target ID。
    def apply_consolidation(
        self,
        *,
        project_namespace: str,
        action: str,
        target_memory_id: str,
        key: str,
        content: str,
        scope: str,
        source_quote: str,
    ) -> LongTermMemoryRecord:
        """验证 consolidation proposal，并原子创建、更新或复用一条记录。"""

        # region 1. 归一化模型提案；authority 字段仍由 Runtime/Service 拥有
        # Model 只提出 typed intent；先把 action/正文/scope 收敛为确定值，再解析存储边界。
        try:
            consolidation_action = MemoryConsolidationAction(action.strip().upper())
        except ValueError as exc:
            raise ValueError(f"unsupported memory action: {action}") from exc
        normalized_key = key.strip()
        normalized_content = content.strip()
        normalized_source_quote = source_quote.strip()
        if not normalized_key or not normalized_content or not normalized_source_quote:
            raise ValueError("memory key, content and source_quote are required")
        if scope not in {memory_scope.value for memory_scope in MemoryScope}:
            raise ValueError(f"unsupported memory scope: {scope}")
        storage_namespace = self._storage_namespace(
            project_namespace=project_namespace,
            scope=scope,
        )
        # endregion 1. 提案归一化结束

        # region 2. CREATE：Runtime 生成 ID、revision 和时间戳
        # CREATE 不接受模型自填 identity；领域校验通过后才由 Repository 原子保存第一版。
        if consolidation_action == MemoryConsolidationAction.CREATE:
            if target_memory_id.strip():
                raise ValueError("CREATE memory action cannot specify target_memory_id")
            now = time.time()
            new_record = LongTermMemoryRecord(
                memory_id=uuid.uuid4().hex,
                namespace=storage_namespace,
                key=normalized_key,
                content=normalized_content,
                source_quotes=[normalized_source_quote],
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
        # endregion 2. CREATE 结束

        # region 3. UPDATE/NOOP：目标必须真实存在于同一 namespace/scope
        normalized_target_id = target_memory_id.strip()
        if not normalized_target_id:
            raise ValueError(f"{consolidation_action.value} requires target_memory_id")
        existing_record = self._require(normalized_target_id)
        if (
            existing_record.namespace != storage_namespace
            or existing_record.scope != scope
        ):
            raise ValueError("memory target is outside the requested namespace or scope")
        if consolidation_action == MemoryConsolidationAction.NOOP:
            return existing_record

        # UPDATE 保留既有 key/identity，只更新语义正文和本次明确 user evidence。
        existing_record.content = normalized_content
        if normalized_source_quote not in existing_record.source_quotes:
            existing_record.source_quotes.append(normalized_source_quote)
        existing_record.revision += 1
        existing_record.updated_at = time.time()
        existing_record.validate()
        self._repository.save(existing_record)
        return existing_record
        # endregion 3. UPDATE/NOOP 结束

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
        query: str = "",
        max_chars: int = 2_000,
    ) -> list[LongTermMemoryRecord]:
        """先应用项目同 key 覆盖，再按 task relevance、scope tie 和 recency 排序。"""

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

        # region 3. task relevance 优先；同分才按项目 scope 和更新时间排序
        query_terms = _lexical_terms(query)
        ranked_records = sorted(
            selected_by_key.values(),
            key=lambda memory_record: (
                -_relevance_score(query_terms, memory_record),
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

    def management_candidates(
        self,
        *,
        namespace: str,
        query: str,
        max_chars: int = 2_000,
    ) -> list[LongTermMemoryRecord]:
        """按最新 human message 返回有界管理候选，供当前 Turn 做 ID 合并。"""

        # region 1. Human-turn query：不复用 Run-start task recall，避免后续 steer 漏掉旧 ID
        if max_chars <= 0:
            return []
        query_terms = _lexical_terms(query)
        visible_records = sorted(
            self.list_for_project(project_namespace=namespace),
            key=lambda memory_record: (
                -_relevance_score(query_terms, memory_record),
                0 if memory_record.scope == MemoryScope.PROJECT.value else 1,
                -memory_record.updated_at,
                memory_record.key.casefold(),
                memory_record.memory_id,
            ),
        )
        # endregion 1. Human-turn query 结束

        # region 2. 有界 Catalog：保留稳定 ID/scope/key/content 供 UPDATE/NOOP
        selected_records: list[LongTermMemoryRecord] = []
        used_chars = 0
        for memory_record in visible_records:
            record_chars = len(memory_record.render_management_line())
            separator_chars = 1 if selected_records else 0
            if used_chars + separator_chars + record_chars > max_chars:
                continue
            selected_records.append(memory_record)
            used_chars += separator_chars + record_chars
        return selected_records
        # endregion 2. 有界 Catalog 结束

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


def _lexical_terms(text: str) -> set[str]:
    """提取轻量中英文词项；英文复数做最小归一化，不引入 retrieval 依赖。"""

    terms = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.casefold())
    return {
        term[:-1] if len(term) > 3 and term.endswith("s") else term
        for term in terms
    }


def _relevance_score(
    query_terms: set[str],
    memory_record: LongTermMemoryRecord,
) -> int:
    if not query_terms:
        return 0
    record_terms = _lexical_terms(f"{memory_record.key} {memory_record.content}")
    return len(query_terms & record_terms)
