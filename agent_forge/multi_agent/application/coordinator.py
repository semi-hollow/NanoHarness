"""顺序角色协作与有界修订的应用层编排。

这条路径不是并发 fanout：Implementer 先产出候选改动，Reviewer/Verifier 通过 Artifact
交接证据，必要时回到下一轮修订。并发 DAG 由 ``LiveFanoutCoordinator`` 负责。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agent_forge.contracts import JsonValue
from agent_forge.observability.domain.event import TraceEventType
from agent_forge.runtime.config import RuntimeConfig

from ..domain.models import AgentProfile, MultiAgentRunSummary, RoleRunResult, RoleSpec
from .dependencies import SequentialCoordinatorDependencies


class MultiAgentCoordinator:
    """协调角色顺序、Artifact 交接和 revision loop。

    可类比支付流程中的工作流编排器：它决定下一岗位是谁、何时退回重做，但每个岗位
    仍通过同一个 AgentLoop 执行。第一遍只读 ``run`` 的四个阶段注释。
    """

    def __init__(
        self,
        task: str,
        profile: AgentProfile,
        runtime_config: RuntimeConfig,
        dependencies: SequentialCoordinatorDependencies,
        *,
        run_dir: str | Path,
        max_revision_rounds: int | None = None,
    ) -> None:
        """接收角色执行、Artifact、candidate diff 和 Event 端口。"""

        self.task = task
        self.profile = profile
        self.base_config = runtime_config
        self.trace = dependencies.events
        self.store = dependencies.artifacts
        self.role_runner = dependencies.role_runner
        self.candidate_diff = dependencies.candidate_diff
        self.run_dir = Path(run_dir)
        self.max_revision_rounds = (
            profile.default_max_revision_rounds
            if max_revision_rounds is None
            else max_revision_rounds
        )
        self._event_step = 0

    # 主要入口：执行 Implementer、Reviewer、Verifier 与有界 revision loop。
    def run(self) -> MultiAgentRunSummary:
        """按角色顺序执行 Implementer、Reviewer、Verifier 和有界修订。"""

        # region 1. 运行初始化：冻结 profile、角色顺序和 revision 上限
        multi_agent_summary = MultiAgentRunSummary(
            run_id=self.trace.run_id,
            task=self.task,
            profile=self.profile.name,
        )
        self._trace(
            "multi_agent_start",
            profile=self.profile.to_dict(),
            max_revision_rounds=self.max_revision_rounds,
        )
        primary_role = self.profile.role_by_name(self.profile.primary_role)
        review_roles = self.profile.ordered_review_roles()
        # endregion 1. 运行初始化结束

        # region 2. 角色状态机：实现 -> 审查/验证 -> 有界返工
        # 阶段 1：主角色生成候选实现；失败时只按现有证据降级，不冒充已验证。
        round_index = 0
        while True:
            primary_result = self._run_role(primary_role, round_index)
            multi_agent_summary.role_results.append(primary_result)
            if primary_result.status == "blocked":
                if self._candidate_diff_exists():
                    multi_agent_summary.status = "patch_generated"
                    multi_agent_summary.final_answer = (
                        f"candidate diff generated; {primary_role.name} stopped after the edit because later "
                        "tool or validation steps were blocked. Treat this as an unverified diff and inspect artifacts."
                    )
                else:
                    multi_agent_summary.status = "blocked"
                    multi_agent_summary.final_answer = primary_result.final_answer
                break
            if primary_result.decision == "NEEDS_REVISION":
                self._trace(
                    "review_decision", decision="NEEDS_REVISION", role=primary_role.name
                )
                if round_index >= self.max_revision_rounds:
                    if self._candidate_diff_exists():
                        multi_agent_summary.status = "patch_generated"
                        multi_agent_summary.final_answer = (
                            f"candidate diff generated; primary role {primary_role.name} still requested revision, "
                            f"but max_revision_rounds={self.max_revision_rounds} was reached."
                        )
                    else:
                        multi_agent_summary.status = "needs_revision"
                        multi_agent_summary.final_answer = (
                            f"primary role {primary_role.name} produced an incomplete artifact, but "
                            f"max_revision_rounds={self.max_revision_rounds} was reached"
                        )
                    break
                round_index += 1
                multi_agent_summary.revision_rounds = round_index
                self._trace(
                    "revision_round",
                    round_index=round_index,
                    requested_by=primary_role.name,
                )
                continue

            # 阶段 2：Reviewer/Verifier 顺序读取 Artifact，并给出通过、退回或阻塞。
            revision_requesting_role_name = ""
            blocking_role_name = ""
            for role in review_roles:
                review_role_result = self._run_role(role, round_index)
                multi_agent_summary.role_results.append(review_role_result)
                if review_role_result.decision == "BLOCKED":
                    blocking_role_name = role.name
                    break
                if (
                    review_role_result.decision == "NEEDS_REVISION"
                    and not revision_requesting_role_name
                ):
                    revision_requesting_role_name = role.name

            if blocking_role_name:
                if self._blocked_after_candidate_diff(blocking_role_name):
                    multi_agent_summary.status = "patch_generated"
                    multi_agent_summary.final_answer = (
                        f"candidate diff generated; {blocking_role_name} could not complete validation. "
                        "Treat this as an unverified diff and inspect artifacts before claiming success."
                    )
                else:
                    multi_agent_summary.status = "blocked"
                    multi_agent_summary.final_answer = (
                        f"blocked by {blocking_role_name}; see artifacts for details"
                    )
                self._trace(
                    "review_decision",
                    success=False,
                    decision="BLOCKED",
                    role=blocking_role_name,
                )
                break

            # 阶段 3：退回请求进入有上限的 revision loop，防止角色无限互相打回。
            if revision_requesting_role_name:
                self._trace(
                    "review_decision",
                    decision="NEEDS_REVISION",
                    role=revision_requesting_role_name,
                )
                if round_index >= self.max_revision_rounds:
                    if self._candidate_diff_exists():
                        multi_agent_summary.status = "patch_generated"
                        multi_agent_summary.final_answer = (
                            f"candidate diff generated; {revision_requesting_role_name} still requested revision, "
                            f"but max_revision_rounds={self.max_revision_rounds} was reached. "
                            "Inspect artifacts before claiming official correctness."
                        )
                    else:
                        multi_agent_summary.status = "needs_revision"
                        multi_agent_summary.final_answer = (
                            f"revision requested by {revision_requesting_role_name}, but max_revision_rounds="
                            f"{self.max_revision_rounds} was reached"
                        )
                    break
                round_index += 1
                multi_agent_summary.revision_rounds = round_index
                self._trace(
                    "revision_round",
                    round_index=round_index,
                    requested_by=revision_requesting_role_name,
                )
                continue

            multi_agent_summary.status = "passed"
            multi_agent_summary.final_answer = (
                "multi-agent run passed reviewer/verifier checks"
            )
            self._trace("review_decision", decision="PASS")
            break
        # endregion 2. 角色状态机结束

        # region 3. 证据收口：冻结最终结论和 Artifact 索引，供报告与评测读取
        final_artifact = self.store.write_text_artifact(
            "Coordinator",
            "final_summary",
            "\n".join(
                [
                    "# Coordinator Final Summary",
                    "",
                    f"- status: `{multi_agent_summary.status}`",
                    f"- revision_rounds: `{multi_agent_summary.revision_rounds}`",
                    "",
                    multi_agent_summary.final_answer,
                ]
            ),
            round_index=round_index,
        )
        multi_agent_summary.artifacts = list(self.store.artifacts)
        multi_agent_summary.final_answer += f"\nFinal artifact: {final_artifact.path}"
        self.store.write_summary(multi_agent_summary)
        self.trace.set_run_context(
            task=self.task,
            stop_reason=multi_agent_summary.status,
            final_answer=multi_agent_summary.final_answer,
        )
        self._trace(
            "multi_agent_done",
            status=multi_agent_summary.status,
            revision_rounds=multi_agent_summary.revision_rounds,
        )
        return multi_agent_summary
        # endregion 3. 证据收口结束

    def _run_role(self, role: RoleSpec, round_index: int) -> RoleRunResult:
        """执行单个角色，并把成功或异常统一转换为可审计 RoleRunResult。

        这里是 Coordinator 与角色 Runtime 的连接点：输入是角色契约，输出不是裸文本，
        而是带 decision、artifact 和 round 的结构化结果。
        """

        # region 1. 角色输入：记录开始事件，并构造该角色专属 task/config/tool scope
        self._trace(
            "agent_stage_start",
            agent_name=role.name,
            role=role.to_dict(),
            round_index=round_index,
        )
        role_task = self._build_role_task(role, round_index)
        role_config = self._role_config(role, round_index)
        allowed_role_tools = self._tools_for_role(role, round_index)
        # endregion 1. 角色输入结束

        try:
            # region 2. 角色执行：调用隔离 Runtime，并物化 decision 与 artifact
            final_answer = self.role_runner.run_role(
                config=role_config,
                allowed_tools=allowed_role_tools,
                task=role_task,
                agent_name=role.name,
            )
            role_decision = self._decision_for_role(role, final_answer)
            role_was_blocked = role_decision == "BLOCKED" or final_answer.startswith(
                "blocked:"
            )
            role_status = "blocked" if role_was_blocked else "completed"
            role_artifact = self.store.write_role_artifact(
                role,
                final_answer,
                round_index,
            )
            role_run_result = RoleRunResult(
                role=role.name,
                status=role_status,
                decision=role_decision,
                artifact_ids=[role_artifact.id],
                final_answer=final_answer,
                round_index=round_index,
            )
            self._trace(
                "artifact_created",
                agent_name=role.name,
                artifact=role_artifact.to_dict(),
                decision=role_decision,
                round_index=round_index,
            )
            self._trace(
                "agent_stage_end",
                agent_name=role.name,
                status=role_status,
                decision=role_decision,
                round_index=round_index,
            )
            if role.name in self.profile.verifier_roles:
                self._trace(
                    "verifier_result",
                    agent_name=role.name,
                    decision=role_decision,
                    round_index=round_index,
                )
            return role_run_result
            # endregion 2. 角色执行结束
        except Exception as exc:
            # region 3. 失败收口：异常也写 Artifact，避免角色证据链断裂
            role_failure_answer = (
                f"blocked: role {role.name} failed with exception: {exc}"
            )
            role_artifact = self.store.write_role_artifact(
                role,
                role_failure_answer,
                round_index,
            )
            self._trace(
                "agent_stage_end",
                success=False,
                agent_name=role.name,
                error=str(exc),
                round_index=round_index,
            )
            return RoleRunResult(
                role=role.name,
                status="blocked",
                decision="BLOCKED",
                artifact_ids=[role_artifact.id],
                final_answer=role_failure_answer,
                round_index=round_index,
                error=str(exc),
            )
            # endregion 3. 失败收口结束

    def _role_config(self, role: RoleSpec, round_index: int) -> RuntimeConfig:
        role_steps = (
            min(self.base_config.max_steps, role.max_steps)
            if self.base_config.max_steps
            else role.max_steps
        )
        approval_mode = "dry-run" if role.read_only else self.base_config.approval_mode
        return replace(
            self.base_config,
            max_steps=role_steps,
            approval_mode=approval_mode,
            task_state_root=str(
                self.run_dir
                / "multi_agent"
                / "task_state"
                / f"r{round_index:02d}-{role.name}"
            ),
        )

    def _tools_for_role(self, role: RoleSpec, round_index: int) -> list[str]:
        if round_index > 0 and role.revision_allowed_tools is not None:
            return role.revision_allowed_tools
        return role.allowed_tools

    def _build_role_task(self, role: RoleSpec, round_index: int) -> str:
        return "\n".join(
            [
                f"You are {role.name}, the {role.role}, in a coordinator-driven multi-agent harness.",
                "",
                "Original task:",
                self.task,
                "",
                "Role instructions:",
                role.instructions,
                "",
                f"Round: {round_index}",
                f"Expected artifact: {role.output_artifact}",
                "Allowed role tools: "
                f"{', '.join(self._tools_for_role(role, round_index)) if self._tools_for_role(role, round_index) else 'none; use artifacts only'}",
                "",
                "Prior artifacts:",
                self.store.render_handoff_context(),
                "",
                "Output requirements:",
                "- Be concise and evidence-grounded.",
                "- Reference artifact paths or file paths when relevant.",
                "- If a prior artifact contains raw tool-call markup or evidence only, replace it with the requested artifact.",
                "- Reviewer/verifier roles must start with PASS, NEEDS_REVISION, or BLOCKED.",
            ]
        )

    def _decision_for_role(self, role: RoleSpec, final_answer: str) -> str:
        if final_answer.startswith("blocked:"):
            return "BLOCKED"
        if self._looks_like_unfinished_tool_output(final_answer):
            return "NEEDS_REVISION"
        decision_candidate_lines = [
            line.strip() for line in (final_answer or "").splitlines() if line.strip()
        ]
        for decision_candidate_line in decision_candidate_lines[:12]:
            normalized_decision_line = _normalize_decision_line(decision_candidate_line)
            if (
                _line_has_marker(normalized_decision_line, role.pass_markers)
                or "VERDICT: PASS" in normalized_decision_line
                or "STATUS: PASS" in normalized_decision_line
            ):
                return "PASS"
            if any(
                marker in normalized_decision_line
                for marker in ("裁决:通过", "裁决: 通过", "结论:通过", "结论: 通过")
            ):
                return "PASS"
            if _line_has_marker(
                normalized_decision_line,
                role.revision_markers,
            ):
                return "NEEDS_REVISION"
            if (
                "VERDICT: NEEDS_REVISION" in normalized_decision_line
                or "STATUS: NEEDS_REVISION" in normalized_decision_line
            ):
                return "NEEDS_REVISION"
            if _line_has_marker(
                normalized_decision_line,
                role.blocked_markers,
            ):
                return "BLOCKED"
            if (
                "VERDICT: BLOCKED" in normalized_decision_line
                or "STATUS: BLOCKED" in normalized_decision_line
            ):
                return "BLOCKED"
        if role.name in {*self.profile.review_roles, *self.profile.verifier_roles}:
            return "NEEDS_REVISION"
        return "COMPLETED"

    def _looks_like_unfinished_tool_output(self, final_answer: str) -> bool:
        final_answer_text = (final_answer or "").strip()
        if not final_answer_text:
            return True
        final_answer_prefix = final_answer_text[:1200]
        raw_tool_markers = (
            "<｜｜DSML｜｜tool_calls>",
            "<tool_calls>",
            '"tool_calls"',
            "function_call",
        )
        return any(marker in final_answer_prefix for marker in raw_tool_markers)

    def _blocked_after_candidate_diff(self, blocking_role_name: str) -> bool:
        if blocking_role_name not in self.profile.verifier_roles:
            return False
        return self._candidate_diff_exists()

    def _candidate_diff_exists(self) -> bool:
        return self.candidate_diff.exists()

    def _trace(
        self,
        event_type: TraceEventType,
        success: bool = True,
        **event_data: JsonValue,
    ) -> None:
        self._event_step += 1
        agent_name = str(event_data.pop("agent_name", "MultiAgentCoordinator"))
        self.trace.record_event(
            step=self._event_step,
            agent_name=agent_name,
            event_type=event_type,
            success=success,
            data=event_data,
        )


def _normalize_decision_line(line: str) -> str:
    return line.strip().strip("*#:- `").replace("：", ":").upper()


def _line_has_marker(line: str, markers: list[str]) -> bool:
    return any(line.startswith(marker.upper()) for marker in markers)
