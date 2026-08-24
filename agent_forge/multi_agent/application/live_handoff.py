"""LIVE coordination 的唯一一致性 Owner。

系统角色：在多个真实 Worker 并发运行时，集中维护 READY/FEEDBACK/UPDATE、mailbox、
版本、generation、attempt 和最终 freshness；它不负责调度 Worker，也不应用 Diff。
输入：已冻结 ``FanoutPlan``、worker-bound publish/drain 请求和集成阶段通知。
输出：可交付语义证据、LIVE readiness、集成授权以及追加式 coordination timeline。
相邻边界：``FanoutCoordinator`` 决定何时启动/集成；``LiveWorkerContext`` 隐藏发布者
身份；``AgentLoop`` 只在 safe model boundary 消费 mailbox。

折叠导航：1 状态容器；2 Attempt 生命周期；3 发布与投递；4 最终集成门禁；
5 Generation/查询；6 锁内不变量；7 Worker-bound facade。
"""

from __future__ import annotations

import time
from threading import Condition, RLock
from typing import Any

from ..domain.live import FanoutPlan
from ..domain.live_handoff import LiveEventType, LiveHandoffEvent
from ..ports import FanoutArtifactPort, LiveWorkerContextPort


class LiveHandoffRuntime:
    """用一个锁维护 LIVE 跨字段不变量；它不调度 Worker，也不应用 Diff。"""

    # region 1. 状态容器：一个 Condition/RLock 保护所有跨字段事实和唤醒版本
    def __init__(self, plan: FanoutPlan, artifacts: FanoutArtifactPort) -> None:
        self.plan = plan
        self.artifacts = artifacts
        self._condition = Condition(RLock())
        self._generation_number = 1
        self._plan_generation_id = self._generation_identity(plan)
        self._state_revision = 0
        self._sequence = 0
        self._attempts: dict[str, int] = {}
        self._worker_states: dict[str, str] = {
            task.id: "pending" for task in plan.tasks
        }
        self._sealed_attempts: dict[str, int] = {}
        self._events: dict[str, LiveHandoffEvent] = {}
        self._latest: dict[tuple[str, str, str], LiveHandoffEvent] = {}
        self._mailboxes: dict[str, list[LiveHandoffEvent]] = {}
        self._consumed: dict[
            tuple[str, str, str], tuple[int, str, int]
        ] = {}
        self._delivered: dict[str, set[str]] = {}
        self._timeline: list[dict[str, Any]] = []
        self._started_at = time.monotonic()

    @property
    def plan_generation_id(self) -> str:
        with self._condition:
            return self._plan_generation_id

    @property
    def state_revision(self) -> int:
        with self._condition:
            return self._state_revision

    @property
    def timeline(self) -> list[dict[str, Any]]:
        with self._condition:
            return [dict(record) for record in self._timeline]
    # endregion 1. 状态容器结束

    # region 2. Attempt 生命周期：retry 原子失效旧发布、消费版本和 mailbox
    def begin_attempt(self, task_id: str, worker_attempt_id: int) -> LiveWorkerContextPort:
        """绑定 Worker Attempt；retry 会原子失效旧发布、mailbox 与消费版本。

        伪代码：验证 Task/Attempt -> 清理同 Task 旧代状态 -> 标记 running
        -> 写 timeline -> 返回注入可信身份的 Worker Context。
        """

        with self._condition:
            self._require_task(task_id)
            # Attempt ID 必须从正整数开始，0 不能代表真实执行。
            if worker_attempt_id < 1:
                raise ValueError("worker_attempt_id must be positive")
            previous = self._attempts.get(task_id)
            # retry 只能递增 Attempt；相同或更小 ID 会混淆事件 provenance。
            if previous is not None and worker_attempt_id <= previous:
                raise ValueError("worker_attempt_id must increase for retry")
            # 已有 Attempt 表示这是 retry，先原子失效它发布/消费的所有瞬时状态。
            if previous is not None:
                self._invalidate_attempt_locked(task_id)
            self._attempts[task_id] = worker_attempt_id
            self._worker_states[task_id] = "running"
            self._append_locked(
                "worker_attempt_started",
                task_id=task_id,
                worker_attempt_id=worker_attempt_id,
            )
            self._bump_locked()
        return LiveWorkerContext(task_id, worker_attempt_id, self)

    def finish_attempt(
        self,
        task_id: str,
        worker_attempt_id: int,
        *,
        success: bool,
    ) -> None:
        """记录 Worker 终态；只有 completed Attempt 才可能进入集成授权。"""

        with self._condition:
            self._require_current_attempt(task_id, worker_attempt_id)
            # 只有 running 可以结束；重复 finish 或已 seal 的 Attempt 都是控制流错误。
            if self._worker_states.get(task_id) != "running":
                raise RuntimeError(f"worker {task_id} is not running")
            self._worker_states[task_id] = "finished" if success else "failed"
            self._append_locked(
                "worker_attempt_finished",
                task_id=task_id,
                worker_attempt_id=worker_attempt_id,
                success=success,
            )
            self._bump_locked()
    # endregion 2. Attempt 生命周期结束

    # region 3. 发布与投递：Runtime 注入身份，模型只填写语义内容和合法 route
    def publish(
        self,
        publisher_task_id: str,
        worker_attempt_id: int,
        *,
        event_type: str,
        target_task_id: str,
        semantic_key: str,
        version: int,
        summary: str,
        evidence: list[str],
        caused_by_event_id: str = "",
    ) -> LiveHandoffEvent:
        """Runtime 注入发布者与代际身份，并在持久化成功后提交内存状态。

        伪代码：规范事件类型 -> 绑定当前 Attempt 身份 -> 校验 route/version/cause
        -> 先追加 JSONL -> 再更新 latest/mailbox -> 唤醒调度器。
        """

        # region 1. 绑定事实：规范事件类型，并从 Worker context 取得可信身份
        # 字符串先转换为枚举，未知事件在进入锁内状态前就失败。
        try:
            normalized_type = LiveEventType(str(event_type).upper())
        except ValueError as exc:
            raise ValueError("event_type must be READY, FEEDBACK, or UPDATE") from exc
        with self._condition:
            self._require_current_attempt(publisher_task_id, worker_attempt_id)
            # 只有正在执行的 Worker 能发布；finished 后不能改变最终版本。
            if self._worker_states.get(publisher_task_id) != "running":
                raise RuntimeError("only a running Worker may publish coordination")
            event = LiveHandoffEvent(
                event_type=normalized_type,
                publisher_task_id=publisher_task_id,
                target_task_id=target_task_id,
                semantic_key=semantic_key,
                version=version,
                summary=summary,
                evidence=tuple(evidence),
                plan_generation_id=self._plan_generation_id,
                worker_attempt_id=worker_attempt_id,
                caused_by_event_id=caused_by_event_id,
            )
            # endregion 1. 绑定事实结束

            # region 2. 原子提交：先校验并持久化，再更新 latest/mailbox 和唤醒调度器
            self._validate_publish_locked(event)
            # event_id 是内容寻址身份；重复内容不能形成第二次 timeline commit。
            if event.event_id in self._events:
                raise ValueError("duplicate coordination event")

            # JSONL 追加是提交屏障：持久化失败时不得修改内存状态。
            self._append_locked(
                "handoff_event",
                task_id=publisher_task_id,
                human_authority=False,
                event=event.to_dict(),
            )
            self._events[event.event_id] = event
            # READY/UPDATE 构成 Producer 的最新版本；FEEDBACK 只是反向因果事实。
            if normalized_type in {LiveEventType.READY, LiveEventType.UPDATE}:
                self._latest[self._route_key(event)] = event
            self._mailboxes.setdefault(target_task_id, []).append(event)
            self._bump_locked()
            # endregion 2. 原子提交结束
            return event

    def drain_mailbox(
        self,
        task_id: str,
        worker_attempt_id: int,
        *,
        boundary: str,
    ) -> list[LiveHandoffEvent]:
        """只在命名的模型安全边界交付当前 generation/attempt 的事实。

        伪代码：验证 boundary/Attempt -> 取出 mailbox -> 丢弃 stale 事件
        -> 记录已交付与已消费版本 -> 写 timeline -> 返回下一 Model Step 输入。
        """

        # boundary 名称用于 Trace 解释事件进入哪个 Model Step，不能为空。
        if not boundary.strip():
            raise ValueError("mailbox drain requires a named safe boundary")
        with self._condition:
            self._require_current_attempt(task_id, worker_attempt_id)
            # mailbox 只能进入 still-running Worker；完成后交付会污染最终 candidate 因果链。
            if self._worker_states.get(task_id) != "running":
                raise RuntimeError("only a running Worker may drain coordination")
            pending = self._mailboxes.pop(task_id, [])
            events = [event for event in pending if self._event_is_current_locked(event)]
            # 没有当前事件时不写空 delivery record，也不增加 state revision。
            if not events:
                return []
            # 每个实际进入模型输入的事件都登记 delivery；正向版本额外登记 consumed。
            for event in events:
                self._delivered.setdefault(task_id, set()).add(event.event_id)
                # 只有 READY/UPDATE 参与最终 freshness 对比，FEEDBACK 用于 UPDATE cause。
                if event.event_type in {LiveEventType.READY, LiveEventType.UPDATE}:
                    self._consumed[
                        (task_id, event.publisher_task_id, event.semantic_key)
                    ] = (event.version, event.event_id, event.worker_attempt_id)
            self._append_locked(
                "mailbox_delivered",
                task_id=task_id,
                worker_attempt_id=worker_attempt_id,
                boundary=boundary,
                human_authority=False,
                event_ids=[event.event_id for event in events],
                model_input_revision=self._state_revision + 1,
            )
            self._bump_locked()
            return events

    def live_ready(self, task_id: str) -> bool:
        """返回调度事实：所有 inbound LIVE edge 已有当前 READY/UPDATE。

        这里只决定“能否提前启动”，不代表最终版本已经消费或可以集成。
        """

        with self._condition:
            # 每条入站 LIVE 边都必须已有当前 generation/Attempt 的正向版本。
            for dependency in self.plan.live_dependencies_for(task_id):
                event = self._latest.get(
                    (
                        dependency.producer_task_id,
                        dependency.target_task_id,
                        dependency.semantic_key,
                    )
                )
                # 缺失或 stale 版本意味着下游仍不能安全开始。
                if event is None or not self._event_is_current_locked(event):
                    return False
                # Producer 已失败时，旧 READY 不能继续解锁 Consumer。
                if self._worker_states.get(dependency.producer_task_id) == "failed":
                    return False
            return True
    # endregion 3. 发布与投递结束

    # region 4. 最终集成门禁：启动可以早，Producer sealed 与最终版本消费不能省略
    def authorize_integration(self, task_id: str, worker_attempt_id: int) -> None:
        """原子冻结 freshness 判断；成功后该 Attempt 不再允许发布新事实。

        伪代码：确认 Worker finished -> 逐条检查 Producer 已集成
        -> 读取 Producer 最终版本 -> 对比 Consumer 实际消费版本 -> 授权集成。
        """

        with self._condition:
            self._require_current_attempt(task_id, worker_attempt_id)
            # Worker 仍在运行时版本可能继续变化，因此不能提前做 freshness 判断。
            if self._worker_states.get(task_id) != "finished":
                raise RuntimeError("Worker must finish before integration authorization")
            # 每条入站 LIVE 边都独立过 Producer seal 与最终版本消费检查。
            for dependency in self.plan.live_dependencies_for(task_id):
                producer = dependency.producer_task_id
                producer_attempt = self._attempts.get(producer)
                # Producer candidate 必须已经真实应用成功，只有发布事件不够。
                if self._sealed_attempts.get(producer) != producer_attempt:
                    raise RuntimeError(
                        f"LIVE producer {producer} is not successfully integrated"
                    )
                latest = self._latest.get(
                    (producer, task_id, dependency.semantic_key)
                )
                consumed = self._consumed.get(
                    (task_id, producer, dependency.semantic_key)
                )
                # latest 必须仍属于当前 generation 和 Producer 当前 Attempt。
                if latest is None or not self._event_is_current_locked(latest):
                    raise RuntimeError(f"LIVE dependency {producer} has no final version")
                # Consumer 必须精确消费最新 event/版本/Attempt，消费旧 v1 不能绑定 Producer v2。
                if consumed != (
                    latest.version,
                    latest.event_id,
                    latest.worker_attempt_id,
                ):
                    raise RuntimeError(
                        f"Worker {task_id} did not consume final LIVE version "
                        f"{producer}:{dependency.semantic_key}:v{latest.version}"
                    )
            self._worker_states[task_id] = "integration_authorized"
            self._append_locked(
                "integration_authorized",
                task_id=task_id,
                worker_attempt_id=worker_attempt_id,
                consumed_versions=self.consumed_versions(task_id),
            )
            self._bump_locked()

    def seal_integration(
        self,
        task_id: str,
        worker_attempt_id: int,
        *,
        success: bool,
    ) -> None:
        """记录 candidate apply 的可信结果，供下游 integration gate 使用。

        成功时保存 sealed Attempt；失败时清理 seal，让依赖它的 Consumer 无法集成。
        """

        with self._condition:
            self._require_current_attempt(task_id, worker_attempt_id)
            # 普通 Worker 可从 finished seal；有 LIVE 入边的 Worker 需先获得 authorization。
            if self._worker_states.get(task_id) not in {
                "finished",
                "integration_authorized",
            }:
                raise RuntimeError("Worker is not ready for integration sealing")
            self._worker_states[task_id] = "integrated" if success else "failed"
            # 只有真实 candidate apply 成功才形成可供下游检查的 sealed_attempt。
            if success:
                self._sealed_attempts[task_id] = worker_attempt_id
            else:
                self._sealed_attempts.pop(task_id, None)
            self._append_locked(
                "integration_sealed",
                task_id=task_id,
                worker_attempt_id=worker_attempt_id,
                success=success,
                final_versions=self.latest_versions_from(task_id),
            )
            self._bump_locked()
    # endregion 4. 最终集成门禁结束

    # region 5. Generation、唤醒与只读查询：replan 后旧事件不能跨代复用
    def replace_plan(self, plan: FanoutPlan) -> None:
        """Remaining-plan replan 开启新代际；旧 mailbox/event 状态不跨代复制。"""

        with self._condition:
            self.plan = plan
            self._generation_number += 1
            self._plan_generation_id = self._generation_identity(plan)
            self._attempts.clear()
            self._worker_states = {task.id: "pending" for task in plan.tasks}
            self._sealed_attempts.clear()
            self._events.clear()
            self._latest.clear()
            self._mailboxes.clear()
            self._consumed.clear()
            self._delivered.clear()
            self._append_locked(
                "plan_generation_replaced",
                plan_digest=plan.digest,
            )
            self._bump_locked()

    def wait_for_change(self, state_revision: int, timeout: float) -> int:
        """等待状态版本变化；revision 检查避免通知先于 wait 时丢失唤醒。"""

        with self._condition:
            # 调用前状态已变化时直接返回；只有版本仍相同才真正阻塞等待。
            if self._state_revision == state_revision:
                self._condition.wait(timeout=max(0.0, timeout))
            return self._state_revision

    def consumed_versions(self, task_id: str) -> dict[str, int]:
        """返回一个 Consumer 已实际接收的 Producer 最终版本视图。"""

        with self._condition:
            return {
                f"{producer}:{semantic_key}": value[0]
                for (consumer, producer, semantic_key), value in sorted(
                    self._consumed.items()
                )
                if consumer == task_id
            }

    def latest_versions_from(self, task_id: str) -> dict[str, int]:
        """返回一个 Producer 当前 attempt 已发布的最新 READY/UPDATE 版本。"""

        with self._condition:
            return {
                f"{target}:{semantic_key}": event.version
                for (publisher, target, semantic_key), event in sorted(
                    self._latest.items()
                )
                if publisher == task_id and self._event_is_current_locked(event)
            }
    # endregion 5. Generation、唤醒与只读查询结束

    # region 6. 锁内不变量：route、因果、attempt freshness、timeline commit 与 notify
    def _validate_publish_locked(self, event: LiveHandoffEvent) -> None:
        """按事件方向校验显式 route、单调版本和 FEEDBACK/UPDATE 因果。

        伪代码：定位正向/反向授权 route -> READY/UPDATE 校验连续版本
        -> UPDATE 证明 FEEDBACK cause -> FEEDBACK 证明目标仍运行且版本确实被消费。
        """

        # forward 对应 Producer -> Consumer 的 READY/UPDATE 授权边。
        forward = next(
            (
                dependency
                for dependency in self.plan.live_dependencies
                if dependency.producer_task_id == event.publisher_task_id
                and dependency.target_task_id == event.target_task_id
                and dependency.semantic_key == event.semantic_key
            ),
            None,
        )
        # reverse 用于验证 Consumer -> Producer 的 FEEDBACK 确实来自同一语义边。
        reverse = next(
            (
                dependency
                for dependency in self.plan.live_dependencies
                if dependency.producer_task_id == event.target_task_id
                and dependency.target_task_id == event.publisher_task_id
                and dependency.semantic_key == event.semantic_key
            ),
            None,
        )
        # 正向事件必须命中 forward route，并维护从 READY v1 开始的连续版本。
        if event.event_type in {LiveEventType.READY, LiveEventType.UPDATE}:
            # 模型不能向计划外 Task/semantic_key 发布事实。
            if forward is None:
                raise ValueError("READY/UPDATE route is not authorized by FanoutPlan")
            latest = self._latest.get(self._route_key(event))
            # READY 只能是该 route 的第一个版本 v1。
            if event.event_type == LiveEventType.READY:
                # 已有 latest 或版本非 1 都代表重复/跳号 READY。
                if event.version != 1 or latest is not None:
                    raise ValueError("READY must publish the first version v1")
            # UPDATE 必须基于当前 latest 严格加一，不能跳号或覆盖。
            elif latest is None or event.version != latest.version + 1:
                raise ValueError("UPDATE must increment the current version by one")
            # UPDATE 除版本连续外，还必须由已交付给 Producer 的 FEEDBACK 触发。
            if event.event_type == LiveEventType.UPDATE:
                self._validate_update_cause_locked(event)
            return
        # 非正向事件只能是 FEEDBACK，必须命中同一语义边的反向 route。
        if reverse is None:
            raise ValueError("FEEDBACK route is not authorized by FanoutPlan")
        # FEEDBACK 只能送给仍在运行的 Producer，完成后不能再改变其轨迹。
        if self._worker_states.get(event.target_task_id) != "running":
            raise ValueError("FEEDBACK target must be a running Worker")
        consumed = self._consumed.get(
            (event.publisher_task_id, event.target_task_id, event.semantic_key)
        )
        # Consumer 只能反馈自己实际消费过的精确版本，不能凭空评论 vN。
        if consumed is None or consumed[0] != event.version:
            raise ValueError("FEEDBACK must reference a version consumed by its publisher")

    def _validate_update_cause_locked(self, event: LiveHandoffEvent) -> None:
        """证明 UPDATE 的 cause 是同代、同 route 且已进入当前 Producer 模型输入的 FEEDBACK。"""

        cause = self._events.get(event.caused_by_event_id)
        # cause 必须引用已经接受的 FEEDBACK event，而不是任意 event_id。
        if cause is None or cause.event_type != LiveEventType.FEEDBACK:
            raise ValueError("UPDATE requires an accepted FEEDBACK cause")
        # 一次性检查代际、方向、semantic key、delivery 和 Attempt freshness。
        if (
            cause.plan_generation_id != self._plan_generation_id
            or cause.target_task_id != event.publisher_task_id
            or cause.publisher_task_id != event.target_task_id
            or cause.semantic_key != event.semantic_key
            or cause.event_id not in self._delivered.get(event.publisher_task_id, set())
            or not self._event_is_current_locked(cause)
        ):
            raise ValueError("UPDATE cause was not validly delivered on this LIVE edge")

    def _invalidate_attempt_locked(self, task_id: str) -> None:
        """Retry 时清除旧 Attempt 产生的 seal、发布、消费和 delivery 状态。"""

        # 旧 candidate 不再可信，先撤销供下游使用的 integration seal。
        self._sealed_attempts.pop(task_id, None)
        # 删除该 Task 作为 Producer 发布的 latest，防止新 Attempt 继承旧版本号。
        self._latest = {
            key: event
            for key, event in self._latest.items()
            if event.publisher_task_id != task_id
        }
        # 删除该 Task 作为 Consumer 或 Producer 参与的消费记录。
        self._consumed = {
            key: value
            for key, value in self._consumed.items()
            if key[0] != task_id and key[1] != task_id
        }
        # 双向清理旧 mailbox：既不保留它发出的事件，也不保留发给旧 Attempt 的反馈。
        self._mailboxes = {
            target: [
                event
                for event in events
                if event.publisher_task_id != task_id and target != task_id
            ]
            for target, events in self._mailboxes.items()
        }
        self._delivered.pop(task_id, None)
        # 重试后的 Consumer 可重新消费仍有效的上游事实，但不重放旧 FEEDBACK。
        self._mailboxes[task_id] = [
            event
            for event in self._latest.values()
            if event.target_task_id == task_id
        ]
        self._append_locked("worker_attempt_superseded", task_id=task_id)

    def _event_is_current_locked(self, event: LiveHandoffEvent) -> bool:
        return (
            event.plan_generation_id == self._plan_generation_id
            and self._attempts.get(event.publisher_task_id)
            == event.worker_attempt_id
        )

    def _require_task(self, task_id: str) -> None:
        # 未出现在冻结计划中的 Task 不能参与 Attempt 或 coordination。
        if task_id not in self._worker_states:
            raise ValueError(f"unknown fanout task: {task_id}")

    def _require_current_attempt(self, task_id: str, worker_attempt_id: int) -> None:
        self._require_task(task_id)
        # Worker-bound Context 必须与 Runtime 当前 Attempt 完全一致，旧 Context 一律失效。
        if self._attempts.get(task_id) != worker_attempt_id:
            raise RuntimeError("worker attempt is stale or was not started")

    @staticmethod
    def _route_key(event: LiveHandoffEvent) -> tuple[str, str, str]:
        return (
            event.publisher_task_id,
            event.target_task_id,
            event.semantic_key,
        )

    def _generation_identity(self, plan: FanoutPlan) -> str:
        return f"g{self._generation_number}-{plan.digest[:16]}"

    def _append_locked(self, record_type: str, **data: Any) -> None:
        self._sequence += 1
        record = {
            "schema_version": 1,
            "sequence": self._sequence,
            "elapsed_ms": int((time.monotonic() - self._started_at) * 1_000),
            "record_type": record_type,
            "plan_generation_id": self._plan_generation_id,
            "state_revision": self._state_revision,
            **data,
        }
        self.artifacts.append_coordination(record)
        self._timeline.append(record)

    def _bump_locked(self) -> None:
        self._state_revision += 1
        self._condition.notify_all()
    # endregion 6. 锁内不变量结束


# region 7. Worker-bound facade：调用方不能覆盖 publisher/generation/attempt 身份
class LiveWorkerContext(LiveWorkerContextPort):
    """只向一个 Worker 暴露绑定后的发布与消费能力。"""

    def __init__(
        self,
        task_id: str,
        worker_attempt_id: int,
        runtime: LiveHandoffRuntime,
    ) -> None:
        self.task_id = task_id
        self.worker_attempt_id = worker_attempt_id
        self._runtime = runtime

    def publish(
        self,
        *,
        event_type: str,
        target_task_id: str,
        semantic_key: str,
        version: int,
        summary: str,
        evidence: list[str],
        caused_by_event_id: str = "",
    ) -> LiveHandoffEvent:
        return self._runtime.publish(
            self.task_id,
            self.worker_attempt_id,
            event_type=event_type,
            target_task_id=target_task_id,
            semantic_key=semantic_key,
            version=version,
            summary=summary,
            evidence=evidence,
            caused_by_event_id=caused_by_event_id,
        )

    def drain_mailbox(self, *, boundary: str) -> list[LiveHandoffEvent]:
        return self._runtime.drain_mailbox(
            self.task_id,
            self.worker_attempt_id,
            boundary=boundary,
        )
# endregion 7. Worker-bound facade 结束
