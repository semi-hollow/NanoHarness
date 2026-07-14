from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agent_forge.contracts import JsonValue
from agent_forge.observability.domain.event import TraceEventType
from agent_forge.runtime.config import RuntimeConfig

from ..domain.models import AgentProfile, MultiAgentRunSummary, RoleRunResult, RoleSpec
from .dependencies import SequentialCoordinatorDependencies


class MultiAgentCoordinator:

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
        """接收角色执行、Artifact、Patch 和 Event 端口。"""

        self.task = task
        self.profile = profile
        self.base_config = runtime_config
        self.trace = dependencies.events
        self.store = dependencies.artifacts
        self.role_runner = dependencies.role_runner
        self.candidate_patch = dependencies.candidate_patch
        self.run_dir = Path(run_dir)
        self.max_revision_rounds = (
            profile.default_max_revision_rounds if max_revision_rounds is None else max_revision_rounds
        )
        self._event_step = 0

    # 主要入口：下方定义承接该模块的核心调用。
    def run(self) -> MultiAgentRunSummary:
        """按角色顺序执行 Implementer、Reviewer、Verifier 和有界修订。"""

        summary = MultiAgentRunSummary(
            run_id=self.trace.run_id,
            task=self.task,
            profile=self.profile.name,
        )
        self._trace("multi_agent_start", profile=self.profile.to_dict(), max_revision_rounds=self.max_revision_rounds)
        primary = self.profile.role_by_name(self.profile.primary_role)
        review_roles = self.profile.ordered_review_roles()

        round_index = 0
        while True:
            primary_result = self._run_role(primary, round_index)
            summary.role_results.append(primary_result)
            if primary_result.status == "blocked":
                if self._candidate_patch_exists():
                    summary.status = "patch_generated"
                    summary.final_answer = (
                        f"candidate patch generated; {primary.name} stopped after the patch because later "
                        "tool or validation steps were blocked. Treat this as an unverified patch and inspect artifacts."
                    )
                else:
                    summary.status = "blocked"
                    summary.final_answer = primary_result.final_answer
                break
            if primary_result.decision == "NEEDS_REVISION":
                self._trace("review_decision", decision="NEEDS_REVISION", role=primary.name)
                if round_index >= self.max_revision_rounds:
                    if self._candidate_patch_exists():
                        summary.status = "patch_generated"
                        summary.final_answer = (
                            f"candidate patch generated; primary role {primary.name} still requested revision, "
                            f"but max_revision_rounds={self.max_revision_rounds} was reached."
                        )
                    else:
                        summary.status = "needs_revision"
                        summary.final_answer = (
                            f"primary role {primary.name} produced an incomplete artifact, but "
                            f"max_revision_rounds={self.max_revision_rounds} was reached"
                        )
                    break
                round_index += 1
                summary.revision_rounds = round_index
                self._trace("revision_round", round_index=round_index, requested_by=primary.name)
                continue

            revision_requested_by = ""
            blocked_by = ""
            for role in review_roles:
                result = self._run_role(role, round_index)
                summary.role_results.append(result)
                if result.decision == "BLOCKED":
                    blocked_by = role.name
                    break
                if result.decision == "NEEDS_REVISION" and not revision_requested_by:
                    revision_requested_by = role.name

            if blocked_by:
                if self._blocked_after_candidate_patch(blocked_by):
                    summary.status = "patch_generated"
                    summary.final_answer = (
                        f"candidate patch generated; {blocked_by} could not complete validation. "
                        "Treat this as an unverified patch and inspect artifacts before claiming success."
                    )
                else:
                    summary.status = "blocked"
                    summary.final_answer = f"blocked by {blocked_by}; see artifacts for details"
                self._trace("review_decision", success=False, decision="BLOCKED", role=blocked_by)
                break

            if revision_requested_by:
                self._trace("review_decision", decision="NEEDS_REVISION", role=revision_requested_by)
                if round_index >= self.max_revision_rounds:
                    if self._candidate_patch_exists():
                        summary.status = "patch_generated"
                        summary.final_answer = (
                            f"candidate patch generated; {revision_requested_by} still requested revision, "
                            f"but max_revision_rounds={self.max_revision_rounds} was reached. "
                            "Inspect artifacts before claiming official correctness."
                        )
                    else:
                        summary.status = "needs_revision"
                        summary.final_answer = (
                            f"revision requested by {revision_requested_by}, but max_revision_rounds="
                            f"{self.max_revision_rounds} was reached"
                        )
                    break
                round_index += 1
                summary.revision_rounds = round_index
                self._trace("revision_round", round_index=round_index, requested_by=revision_requested_by)
                continue

            summary.status = "passed"
            summary.final_answer = "multi-agent run passed reviewer/verifier checks"
            self._trace("review_decision", decision="PASS")
            break

        final_artifact = self.store.write_text_artifact(
            "Coordinator",
            "final_summary",
            "\n".join(
                [
                    "# Coordinator Final Summary",
                    "",
                    f"- status: `{summary.status}`",
                    f"- revision_rounds: `{summary.revision_rounds}`",
                    "",
                    summary.final_answer,
                ]
            ),
            round_index=round_index,
        )
        summary.artifacts = list(self.store.artifacts)
        summary.final_answer += f"\nFinal artifact: {final_artifact.path}"
        self.store.write_summary(summary)
        self.trace.set_run_context(task=self.task, stop_reason=summary.status, final_answer=summary.final_answer)
        self._trace("multi_agent_done", status=summary.status, revision_rounds=summary.revision_rounds)
        return summary

    def _run_role(self, role: RoleSpec, round_index: int) -> RoleRunResult:

        self._trace("agent_stage_start", agent_name=role.name, role=role.to_dict(), round_index=round_index)
        role_task = self._build_role_task(role, round_index)
        role_config = self._role_config(role, round_index)
        try:
            final_answer = self.role_runner.run_role(
                config=role_config,
                allowed_tools=self._tools_for_role(role, round_index),
                task=role_task,
                agent_name=role.name,
            )
            decision = self._decision_for_role(role, final_answer)
            status = "blocked" if decision == "BLOCKED" or final_answer.startswith("blocked:") else "completed"
            artifact = self.store.write_role_artifact(role, final_answer, round_index)
            result = RoleRunResult(
                role=role.name,
                status=status,
                decision=decision,
                artifact_ids=[artifact.id],
                final_answer=final_answer,
                round_index=round_index,
            )
            self._trace(
                "artifact_created",
                agent_name=role.name,
                artifact=artifact.to_dict(),
                decision=decision,
                round_index=round_index,
            )
            self._trace(
                "agent_stage_end",
                agent_name=role.name,
                status=status,
                decision=decision,
                round_index=round_index,
            )
            if role.name in self.profile.verifier_roles:
                self._trace("verifier_result", agent_name=role.name, decision=decision, round_index=round_index)
            return result
        except Exception as exc:
            content = f"blocked: role {role.name} failed with exception: {exc}"
            artifact = self.store.write_role_artifact(role, content, round_index)
            self._trace("agent_stage_end", success=False, agent_name=role.name, error=str(exc), round_index=round_index)
            return RoleRunResult(
                role=role.name,
                status="blocked",
                decision="BLOCKED",
                artifact_ids=[artifact.id],
                final_answer=content,
                round_index=round_index,
                error=str(exc),
            )

    def _role_config(self, role: RoleSpec, round_index: int) -> RuntimeConfig:

        role_steps = min(self.base_config.max_steps, role.max_steps) if self.base_config.max_steps else role.max_steps
        approval_mode = "dry-run" if role.read_only else getattr(self.base_config, "approval_mode", "trusted")
        return replace(
            self.base_config,
            max_steps=role_steps,
            approval_mode=approval_mode,
            task_state_root=str(self.run_dir / "multi_agent" / "task_state" / f"r{round_index:02d}-{role.name}"),
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
        lines = [line.strip() for line in (final_answer or "").splitlines() if line.strip()]
        for line in lines[:12]:
            normalized = _normalize_decision_line(line)
            if _line_has_marker(normalized, role.pass_markers) or "VERDICT: PASS" in normalized or "STATUS: PASS" in normalized:
                return "PASS"
            if "裁决:通过" in normalized or "裁决: 通过" in normalized or "结论:通过" in normalized or "结论: 通过" in normalized:
                return "PASS"
            if _line_has_marker(normalized, role.revision_markers):
                return "NEEDS_REVISION"
            if "VERDICT: NEEDS_REVISION" in normalized or "STATUS: NEEDS_REVISION" in normalized:
                return "NEEDS_REVISION"
            if _line_has_marker(normalized, role.blocked_markers):
                return "BLOCKED"
            if "VERDICT: BLOCKED" in normalized or "STATUS: BLOCKED" in normalized:
                return "BLOCKED"
        if role.name in {*self.profile.review_roles, *self.profile.verifier_roles}:
            return "NEEDS_REVISION"
        return "COMPLETED"

    def _looks_like_unfinished_tool_output(self, final_answer: str) -> bool:

        text = (final_answer or "").strip()
        if not text:
            return True
        head = text[:1200]
        raw_tool_markers = (
            "<｜｜DSML｜｜tool_calls>",
            "<tool_calls>",
            '"tool_calls"',
            "function_call",
        )
        return any(marker in head for marker in raw_tool_markers)

    def _blocked_after_candidate_patch(self, blocked_by: str) -> bool:

        if blocked_by not in self.profile.verifier_roles:
            return False
        return self._candidate_patch_exists()

    def _candidate_patch_exists(self) -> bool:

        return self.candidate_patch.exists()

    def _trace(
        self,
        event_type: TraceEventType,
        success: bool = True,
        **kwargs: JsonValue,
    ) -> None:

        self._event_step += 1
        agent_name = str(kwargs.pop("agent_name", "MultiAgentCoordinator"))
        self.trace.record_event(
            step=self._event_step,
            agent_name=agent_name,
            event_type=event_type,
            success=success,
            data=kwargs,
        )


def _normalize_decision_line(line: str) -> str:

    return line.strip().strip("*#:- `").replace("：", ":").upper()


def _line_has_marker(line: str, markers: list[str]) -> bool:

    return any(line.startswith(marker.upper()) for marker in markers)
