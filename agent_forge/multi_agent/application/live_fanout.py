"""Live fanout 的调度、冲突门和恢复编排。"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from agent_forge.runtime.config import RuntimeConfig

from ..domain.fanout import (
    FanoutConflict,
    SubagentResult,
    SubagentTask,
    build_conflict_free_batches,
    detect_result_conflicts,
)
from ..domain.live import (
    FanoutCheckpoint,
    FanoutPlan,
    LiveFanoutSummary,
    LiveSubagentResult,
    aggregate_live_metrics,
)
from .dependencies import LiveFanoutDependencies


class LiveFanoutCoordinator:
    """协调真实 AgentLoop worker，但不实现 Git、文件或 worker runtime。

    ``run`` 是公开主入口。它拥有依赖批次、动态冲突、diff 合并顺序、恢复资格和
    最终状态；所有外部 IO 和状态变更均通过 ``LiveFanoutDependencies`` 完成。
    """

    def __init__(
        self,
        *,
        plan: FanoutPlan,
        base_config: RuntimeConfig,
        dependencies: LiveFanoutDependencies,
        max_workers: int = 4,
        resume_from: str | None = None,
    ) -> None:
        self.plan = plan
        self.base_config = base_config
        self.events = dependencies.events
        self.workspace = dependencies.workspace
        self.artifacts = dependencies.artifacts
        self.workers = dependencies.workers
        self.max_workers = max(1, min(int(max_workers), 8))
        self.resume_from = resume_from

    # 主要入口：这是多 Agent 用例的完整主链，编号注释标明各执行阶段。
    def run(self) -> LiveFanoutSummary:
        """执行 dependency-aware worker，并返回可审计的集成结果。

        主链分成六步：
        1. 固定集成基线并检查写任务前提；
        2. 根据依赖和声明写入范围生成无冲突批次；
        3. 在隔离 workspace 中并发运行同批 Worker；
        4. 根据 Worker 实际改动文件做动态冲突检查；
        5. 按稳定顺序检查并合并 candidate diff；
        6. 全部任务成功且无冲突时，运行只读 Finalizer 并发布证据。

        ``build_conflict_free_batches`` 处理执行前可知的静态冲突；
        ``_mark_dynamic_conflicts`` 处理 Worker 运行后才知道的实际文件冲突；
        ``_merge_batch`` 才真正把 candidate diff 写入集成 workspace。
        """

        # region 1. 集成前提（主链）：固定基线，写任务必须可安全自动恢复
        started_at = time.monotonic()
        base_revision = self.workspace.head()
        if not base_revision:
            raise RuntimeError("live fanout requires a git workspace")

        # Live Fanout 的 Worker 使用临时 worktree。若写操作等待逐次人工审批，
        # 临时环境退出后无法原地续跑，所以这里只接受已显式开启自动审批的写任务。
        contains_write_tasks = any(task.write_scope for task in self.plan.tasks)
        if contains_write_tasks and not self.base_config.auto_approve_writes:
            raise RuntimeError(
                "live fanout manual write approval is not recoverable across "
                "ephemeral worktrees; use single/multi mode for per-operation approval"
            )
        if contains_write_tasks and self.workspace.status():
            raise RuntimeError("write fanout requires a clean integration workspace")

        # 第 2 步：根据 depends_on 和声明的 write_scope 建批次。
        # 同批任务可以并发；存在依赖或静态写入冲突的任务会进入后续批次。
        dependency_batches = build_conflict_free_batches(self.plan.tasks)
        batch_task_ids = [[task.id for task in batch] for batch in dependency_batches]

        # 主链状态：successful_task_ids 只包含已经完成且成功集成的任务，
        # 因此它既是后续依赖门禁，也是 Finalizer 是否可运行的判断依据。
        all_worker_results: list[LiveSubagentResult] = []
        merged_task_ids: list[str] = []
        successful_task_ids: set[str] = set()
        detected_conflicts: list[FanoutConflict] = []
        # endregion 1. 集成前提结束

        # region 2. 依赖调度与批次执行（主链）：恢复历史结果，同批并发，批间串行合并
        # 先发布经过固定的计划和批次。此事件只表示“准备执行”，不表示任务成功。
        self._record_fanout_started(
            batch_task_ids=batch_task_ids,
        )

        # 恢复时先校验 plan/base/diff 摘要，再把历史成功 diff 重放到当前集成 workspace。
        # 恢复完成的任务会进入 successful_task_ids，后续不会再次执行。
        if self.resume_from:
            restored_results = self._restore_previous(base_revision)
            all_worker_results.extend(restored_results)
            successful_task_ids.update(
                worker_result.task_id for worker_result in restored_results
            )
            merged_task_ids.extend(
                worker_result.task_id for worker_result in restored_results
            )

        # 在任何新 Worker 启动前保存计划和第一个恢复点，确保中断后有确定入口。
        self.artifacts.write_plan(self.plan)
        self._checkpoint(
            base_revision,
            all_worker_results,
            merged_task_ids,
            "running",
        )

        # 第 3 至 5 步：每个批次内部并发执行，批次之间串行集成。
        for batch_index, batch in enumerate(dependency_batches):
            # 依赖门禁：只启动前置任务已经成功集成的任务；依赖失败则记录 blocked_dependency。
            runnable_tasks = self._runnable_tasks(
                batch,
                successful_task_ids,
                all_worker_results,
                batch_index,
            )
            if not runnable_tasks:
                self._checkpoint(
                    base_revision,
                    all_worker_results,
                    merged_task_ids,
                    "running",
                )
                continue

            # Worker 执行：每个任务在独立 workspace 中运行真实 AgentLoop，
            # 返回 candidate diff 和 touched_files，不直接修改主集成 workspace。
            completed_batch_results = self._run_batch(
                runnable_tasks,
                batch_index,
                self.workspace.diff(),
            )

            # 动态冲突门：根据实际 touched_files 检查同批 Worker 是否碰到同一文件。
            # 命中冲突的结果会改成 dynamic_conflict，禁止进入下一步合并。
            batch_conflicts = self._mark_dynamic_conflicts(completed_batch_results)
            detected_conflicts.extend(batch_conflicts)

            # 真正合并点：按计划中的稳定任务顺序执行“先 check、后 apply”。
            # 空 diff、不可应用 diff 和应用失败都会留下明确状态，不会记为依赖成功。
            self._merge_batch(
                runnable_tasks,
                completed_batch_results,
                successful_task_ids,
                merged_task_ids,
                detected_conflicts,
            )

            # 合并后再汇总 Worker 结果并持久化恢复点，避免 checkpoint 声称尚未落地的成功。
            all_worker_results.extend(completed_batch_results)
            self._checkpoint(
                base_revision,
                all_worker_results,
                merged_task_ids,
                "running",
            )
            self._record_fanout_batch_completed(
                step=batch_index + 1,
                runnable_tasks=runnable_tasks,
                completed_batch_results=completed_batch_results,
                batch_conflicts=batch_conflicts,
            )
        # endregion 2. 依赖调度与批次执行结束

        # region 3. Finalizer 与证据发布（主链）：只有全部成功且无冲突才允许最终判定
        # 先把集成 workspace 当前状态固化为最终 candidate diff artifact。
        # 这一步只发布候选改动，不等于 Finalizer 已经判定通过。
        integrated_diff_file = self.artifacts.write_integrated_diff(
            self.workspace.diff()
        )

        # Finalizer 是最后的 correctness gate，不负责合并。
        # 只有每个计划任务都已成功集成，且静态/动态/应用冲突均为空，才允许启动只读复核。
        every_task_succeeded = len(successful_task_ids) == len(self.plan.tasks)
        finalizer_result = None
        if every_task_succeeded and not detected_conflicts:
            finalizer_result = self.workers.run_finalizer(
                self.plan.goal,
                all_worker_results,
            )

        # 状态裁决集中在 _fanout_status：冲突优先于部分失败，部分失败优先于 Finalizer 结论。
        # 因此 Worker 没有全部成功时，不会因为缺少 Finalizer 结果而被误写成 needs_revision。
        finalizer_decision = finalizer_result.decision if finalizer_result else ""
        fanout_status = _fanout_status(
            worker_results=all_worker_results,
            detected_conflicts=detected_conflicts,
            every_task_succeeded=every_task_succeeded,
            finalizer_decision=finalizer_decision,
        )

        # 汇总层只组装已发生的事实：Worker 结果、冲突、用量、Finalizer 证据和集成 diff 路径。
        # 它不再次执行工具，也不修改任何任务状态。
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        finalizer_usage_summary = (
            finalizer_result.usage_summary if finalizer_result else {}
        )
        fanout_summary = LiveFanoutSummary(
            run_id=self.events.run_id,
            goal=self.plan.goal,
            status=fanout_status,
            plan_digest=self.plan.digest,
            base_head=base_revision,
            batches=batch_task_ids,
            results=all_worker_results,
            merged_task_ids=merged_task_ids,
            conflicts=detected_conflicts,
            wall_time_ms=elapsed_ms,
            metrics=aggregate_live_metrics(
                all_worker_results,
                elapsed_ms,
                max_workers=self.max_workers,
                finalizer_usage=finalizer_usage_summary,
            ),
            final_decision=finalizer_decision,
            final_answer=finalizer_result.answer if finalizer_result else "",
            finalizer_trace_path=(
                finalizer_result.trace_path if finalizer_result else ""
            ),
            finalizer_usage_path=(
                finalizer_result.usage_path if finalizer_result else ""
            ),
            finalizer_usage_summary=finalizer_usage_summary,
            integrated_diff_path=integrated_diff_file,
        )

        # 最终持久化顺序：先写可恢复 checkpoint，再写面向人/Workbench 的 summary，
        # 最后发布完成事件。即使 summary 发布中断，恢复状态也不会丢失。
        self._checkpoint(
            base_revision,
            all_worker_results,
            merged_task_ids,
            fanout_status,
        )
        self.artifacts.write_summary(fanout_summary)
        self._record_fanout_completed(
            step=len(dependency_batches) + 2,
            fanout_summary=fanout_summary,
        )
        # endregion 3. Finalizer 与证据发布结束
        return fanout_summary

    # region 证据记录器
    def _record_fanout_started(self, *, batch_task_ids: list[list[str]]) -> None:
        """记录固定后的计划和依赖批次，不代表 worker 已经执行。"""

        self.events.add(
            0,
            "LiveFanoutCoordinator",
            "fanout_start",
            plan=self.plan.to_dict(),
            batches=batch_task_ids,
        )

    def _record_fanout_batch_completed(
        self,
        *,
        step: int,
        runnable_tasks: list[SubagentTask],
        completed_batch_results: list[LiveSubagentResult],
        batch_conflicts: list[FanoutConflict],
    ) -> None:
        """记录一个并发批次的 worker 结果和动态冲突。"""

        self.events.add(
            step,
            "LiveFanoutCoordinator",
            "fanout_batch_done",
            batch=[task.id for task in runnable_tasks],
            results=[
                worker_result.to_dict() for worker_result in completed_batch_results
            ],
            conflicts=[asdict(conflict) for conflict in batch_conflicts],
        )

    def _record_fanout_completed(
        self,
        *,
        step: int,
        fanout_summary: LiveFanoutSummary,
    ) -> None:
        """记录 Fanout 的最终治理状态和聚合指标。"""

        self.events.add(
            step,
            "LiveFanoutCoordinator",
            "fanout_done",
            success=fanout_summary.status == "passed",
            status=fanout_summary.status,
            metrics=fanout_summary.metrics,
        )

    # endregion 证据记录器结束

    # region 调度、合并与恢复细节
    def _runnable_tasks(
        self,
        batch: list[SubagentTask],
        successful_task_ids: set[str],
        all_worker_results: list[LiveSubagentResult],
        batch_index: int,
    ) -> list[SubagentTask]:
        """返回依赖已完成的任务，并为未满足依赖的任务落一条结果。"""

        runnable_tasks: list[SubagentTask] = []
        for task in batch:
            if task.id in successful_task_ids:
                continue
            if set(task.depends_on).issubset(successful_task_ids):
                runnable_tasks.append(task)
            else:
                all_worker_results.append(
                    LiveSubagentResult(
                        task_id=task.id,
                        status="blocked_dependency",
                        batch_index=batch_index,
                        error="one or more dependencies did not complete",
                    )
                )
        return runnable_tasks

    def _run_batch(
        self,
        tasks: list[SubagentTask],
        batch_index: int,
        base_diff_text: str,
    ) -> list[LiveSubagentResult]:
        """用 ``ThreadPoolExecutor`` 并发运行同批 Worker，再按计划顺序返回结果。

        每个 Future 对应一个隔离任务；单个 Worker 抛出的异常会转换为 ``failed`` 结果，
        不取消同批其他任务。完成先后只影响等待顺序，不影响后续合并顺序。
        """

        # 并发收集容器：future 完成顺序不稳定，所以先按 task_id 建索引。
        results_by_task_id: dict[str, LiveSubagentResult] = {}
        worker_count = max(1, min(self.max_workers, len(tasks)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            task_by_worker_future = {
                executor.submit(
                    self.workers.run_worker,
                    task,
                    batch_index,
                    base_diff_text,
                ): task
                for task in tasks
            }
            for future in as_completed(task_by_worker_future):
                task = task_by_worker_future[future]
                try:
                    results_by_task_id[task.id] = future.result()
                except Exception as exc:
                    results_by_task_id[task.id] = LiveSubagentResult(
                        task_id=task.id,
                        status="failed",
                        batch_index=batch_index,
                        error=str(exc),
                    )
        return [results_by_task_id[task.id] for task in tasks]

    def _mark_dynamic_conflicts(
        self,
        batch_results: list[LiveSubagentResult],
    ) -> list[FanoutConflict]:
        """把 worker 实际触碰文件形成的动态冲突写回任务状态。"""

        # 只有完成的 Worker 才有可参与集成的真实改动范围；失败结果不进入文件冲突比较。
        detected_conflicts = detect_result_conflicts(
            [
                SubagentResult(
                    task_id=worker_result.task_id,
                    status=worker_result.status,
                    touched_files=worker_result.touched_files,
                    batch_index=worker_result.batch_index,
                )
                for worker_result in batch_results
                if worker_result.status == "completed"
            ]
        )
        # 将领域层返回的冲突事实写回每个 Worker 结果，后续 _merge_batch 会跳过这些结果。
        conflicting_task_ids = {
            task_id for conflict in detected_conflicts for task_id in conflict.task_ids
        }
        for worker_result in batch_results:
            if worker_result.task_id in conflicting_task_ids:
                worker_result.status = "dynamic_conflict"
        return detected_conflicts

    def _merge_batch(
        self,
        tasks: list[SubagentTask],
        batch_results: list[LiveSubagentResult],
        successful_task_ids: set[str],
        merged_task_ids: list[str],
        detected_conflicts: list[FanoutConflict],
    ) -> None:
        """按计划顺序检查并应用本批次通过动态冲突检查的 candidate diff。

        写任务先做非空与 ``check_only`` 检查，再真正应用；失败只标记当前任务。
        只有读任务完成或写任务成功合并后，才加入成功集合并解锁依赖任务。
        """

        # batch_results 来自并发 Future，先映射回原计划任务，随后按稳定结果顺序处理。
        task_by_id = {task.id: task for task in tasks}
        for worker_result in batch_results:
            task = task_by_id[worker_result.task_id]

            # failed、blocked_dependency 和 dynamic_conflict 都不能进入集成 workspace。
            if worker_result.status != "completed":
                continue
            if task.write_scope:
                # 写任务必须提供非空 candidate diff；只有最终答案而没有改动不能算任务成功。
                candidate_diff_text = (
                    self.artifacts.read_text(worker_result.candidate_diff_path)
                    if worker_result.candidate_diff_path
                    else ""
                )
                if not candidate_diff_text.strip():
                    worker_result.status = "no_patch"
                    continue

                # 第一遍只做适用性检查，不修改集成 workspace，作用类似数据库事务提交前校验。
                diff_is_applicable, applicability_detail = (
                    self.workspace.apply_unified_diff(
                        candidate_diff_text,
                        check_only=True,
                    )
                )
                if not diff_is_applicable:
                    _record_merge_conflict(
                        worker_result=worker_result,
                        detected_conflicts=detected_conflicts,
                        failure_detail=(
                            f"candidate diff apply check failed: {applicability_detail}"
                        ),
                    )
                    continue

                # 检查通过后才真正应用 diff；检查与应用之间仍可能发生失败，因此两步都要判断。
                diff_was_applied, application_detail = (
                    self.workspace.apply_unified_diff(
                        candidate_diff_text,
                        check_only=False,
                    )
                )
                if not diff_was_applied:
                    _record_merge_conflict(
                        worker_result=worker_result,
                        detected_conflicts=detected_conflicts,
                        failure_detail=(
                            f"candidate diff apply failed: {application_detail}"
                        ),
                    )
                    continue

            # 读任务完成，或写任务已经成功合并后，才能解锁依赖它的后续任务。
            successful_task_ids.add(worker_result.task_id)
            merged_task_ids.append(worker_result.task_id)

    def _restore_previous(self, base_revision: str) -> list[LiveSubagentResult]:
        """校验计划、Git 基线和 patch 哈希，再恢复已合并任务的集成结果。

        所有历史 diff 先在临时环境联合验证，再一次性应用到当前集成 workspace；
        任一身份、文件或适用性检查失败都会拒绝恢复，未完成任务不在这里伪造结果。
        """

        if not self.resume_from:
            return []

        # 恢复身份门：计划和 Git 基线必须与 checkpoint 完全一致，避免把旧任务结果接到新代码上。
        resume_payload = self.artifacts.load_resume(self.resume_from)
        if resume_payload.get("plan_digest") != self.plan.digest:
            raise RuntimeError("fanout resume plan digest does not match")
        if resume_payload.get("base_head") != base_revision:
            raise RuntimeError("fanout resume base commit does not match")
        # checkpoint 是外部持久化数据，先建立显式索引并拒绝当前计划中不存在的任务。
        previous_result_by_task_id = {
            str(saved_result.get("task_id")): saved_result
            for saved_result in resume_payload.get("results", [])
            if isinstance(saved_result, dict)
        }
        previously_merged_task_ids = set(resume_payload.get("merged_task_ids") or [])
        planned_task_ids = {task.id for task in self.plan.tasks}
        unknown_merged_task_ids = sorted(previously_merged_task_ids - planned_task_ids)
        if unknown_merged_task_ids:
            raise RuntimeError(
                "fanout resume contains unknown merged tasks: "
                f"{', '.join(unknown_merged_task_ids)}"
            )

        # 逐任务恢复：重建结果对象，并用 SHA-256 确认 candidate diff 没有在落盘后被替换。
        # 容器中的每项同时保存任务定义、结果快照和待重放 candidate diff。
        prepared_recovery_items: list[tuple[SubagentTask, LiveSubagentResult, str]] = []
        for task in self.plan.tasks:
            if task.id not in previously_merged_task_ids:
                continue
            previous_result_payload = previous_result_by_task_id.get(task.id)
            if not previous_result_payload:
                raise RuntimeError(
                    f"fanout resume has no result for merged task: {task.id}"
                )
            if previous_result_payload.get("status") != "completed":
                raise RuntimeError(
                    f"fanout resume merged task is not completed: {task.id}"
                )
            restored_result_payload = dict(previous_result_payload)
            restored_result_payload["resumed"] = True
            restored_result = LiveSubagentResult(**restored_result_payload)
            candidate_diff_text = ""
            if task.write_scope:
                candidate_diff_path = str(
                    previous_result_payload.get("candidate_diff_path") or ""
                )
                if not candidate_diff_path:
                    raise RuntimeError("fanout resume candidate diff is missing")
                try:
                    candidate_diff_text = self.artifacts.read_text(candidate_diff_path)
                except FileNotFoundError as exc:
                    raise RuntimeError(
                        "fanout resume candidate diff is missing: "
                        f"{candidate_diff_path}"
                    ) from exc
                expected_digest = str(
                    previous_result_payload.get("candidate_diff_sha256") or ""
                )
                actual_digest = hashlib.sha256(
                    candidate_diff_text.encode("utf-8")
                ).hexdigest()
                if not expected_digest or actual_digest != expected_digest:
                    raise RuntimeError(
                        "fanout resume candidate diff digest does not match "
                        f"for {task.id}"
                    )
            prepared_recovery_items.append((task, restored_result, candidate_diff_text))

        # 先在临时环境联合校验所有历史 diff，再一次性应用到当前集成 workspace。
        # 这样不会出现前几个任务已恢复、后一个任务失败而留下半恢复状态。
        recovery_diffs = [
            (task.id, candidate_diff_text)
            for task, _, candidate_diff_text in prepared_recovery_items
            if candidate_diff_text
        ]
        if recovery_diffs:
            combined_diff = self.workers.validate_recovery_diffs(recovery_diffs)
            diff_is_applicable, applicability_detail = (
                self.workspace.apply_unified_diff(
                    combined_diff,
                    check_only=True,
                )
            )
            if not diff_is_applicable:
                raise RuntimeError(
                    f"fanout resume integration check failed: {applicability_detail}"
                )
            diff_was_applied, application_detail = self.workspace.apply_unified_diff(
                combined_diff,
                check_only=False,
            )
            if not diff_was_applied:
                raise RuntimeError(
                    f"fanout resume integration failed: {application_detail}"
                )
        return [
            restored_worker_result
            for _, restored_worker_result, _ in prepared_recovery_items
        ]

    def _checkpoint(
        self,
        base_head: str,
        worker_results: list[LiveSubagentResult],
        merged_task_ids: list[str],
        fanout_status: str,
    ) -> None:
        """原子保存 plan/base、Worker 结果、已合并任务和当前 Fanout 状态。

        该快照用于验证并恢复已验收成果；它记录阶段性事实，不代表全部任务或 Finalizer
        已经通过。写入原子性由 ``FanoutArtifactPort`` 的实现负责。
        """

        self.artifacts.write_checkpoint(
            FanoutCheckpoint(
                plan_digest=self.plan.digest,
                base_head=base_head,
                results=worker_results,
                merged_task_ids=merged_task_ids,
                status=fanout_status,
            )
        )

    # endregion 调度、合并与恢复细节结束


def _record_merge_conflict(
    *,
    worker_result: LiveSubagentResult,
    detected_conflicts: list[FanoutConflict],
    failure_detail: str,
) -> None:
    """把某个 worker 的合并失败同时写回结果和冲突集合。"""

    worker_result.status = "merge_conflict"
    worker_result.error = failure_detail
    detected_conflicts.append(FanoutConflict([worker_result.task_id], failure_detail))


def _fanout_status(
    *,
    worker_results: list[LiveSubagentResult],
    detected_conflicts: list[FanoutConflict],
    every_task_succeeded: bool,
    finalizer_decision: str,
) -> str:
    """根据冲突、任务完成度和 finalizer 决策计算最终 Fanout 状态。"""

    conflict_statuses = {"scope_violation", "dynamic_conflict", "merge_conflict"}
    if detected_conflicts or any(
        worker_result.status in conflict_statuses for worker_result in worker_results
    ):
        return "conflict_resolution_required"
    if not every_task_succeeded:
        return "partial_failure"
    if finalizer_decision == "PASS":
        return "passed"
    return "needs_revision"
