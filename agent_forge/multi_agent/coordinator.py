from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.tools.registry import ToolRegistry

from .artifacts import ArtifactStore
from .types import AgentProfile, MultiAgentRunSummary, RoleRunResult, RoleSpec


class MultiAgentCoordinator:
    """Deterministic coordinator that reuses AgentLoop for each role.

    The coordinator is deliberately not a swarm, quorum system, or peer-to-peer
    chat room. It owns the workflow order, passes information through explicit
    artifacts, and lets reviewer/verifier roles trigger bounded revision rounds.
    """

    def __init__(
        self,
        task: str,
        profile: AgentProfile,
        runtime_config,
        trace,
        registry,
        llm,
        *,
        run_dir: str | Path,
        max_revision_rounds: int | None = None,
    ):
        """Keep shared dependencies while leaving AgentLoop as canonical runtime."""

        self.task = task
        self.profile = profile
        self.base_config = runtime_config
        self.trace = trace
        self.registry = registry
        self.llm = llm
        self.run_dir = Path(run_dir)
        self.max_revision_rounds = (
            profile.default_max_revision_rounds if max_revision_rounds is None else max_revision_rounds
        )
        self.store = ArtifactStore(self.run_dir)
        self._event_step = 0

    def run(self) -> MultiAgentRunSummary:
        """Run the profile and write multi-agent artifacts/reports."""

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
                summary.status = "blocked"
                summary.final_answer = primary_result.final_answer
                break
            if primary_result.decision == "NEEDS_REVISION":
                self._trace("review_decision", decision="NEEDS_REVISION", role=primary.name)
                if round_index >= self.max_revision_rounds:
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
                summary.status = "blocked"
                summary.final_answer = f"blocked by {blocked_by}; see artifacts for details"
                self._trace("review_decision", success=False, decision="BLOCKED", role=blocked_by)
                break

            if revision_requested_by:
                self._trace("review_decision", decision="NEEDS_REVISION", role=revision_requested_by)
                if round_index >= self.max_revision_rounds:
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
        """Run one role through AgentLoop and persist its final answer."""

        self._trace("agent_stage_start", agent_name=role.name, role=role.to_dict(), round_index=round_index)
        role_task = self._build_role_task(role, round_index)
        role_config = self._role_config(role, round_index)
        role_registry = self._role_registry(role, round_index)
        try:
            final_answer = AgentLoop(role_config, self.trace, role_registry, self.llm).run(role_task, agent_name=role.name)
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

    def _role_config(self, role: RoleSpec, round_index: int):
        """Derive a role-specific RuntimeConfig without mutating the base config."""

        role_steps = min(self.base_config.max_steps, role.max_steps) if self.base_config.max_steps else role.max_steps
        approval_mode = "dry-run" if role.read_only else getattr(self.base_config, "approval_mode", "trusted")
        return replace(
            self.base_config,
            max_steps=role_steps,
            approval_mode=approval_mode,
            task_state_root=str(self.run_dir / "multi_agent" / "task_state" / f"r{round_index:02d}-{role.name}"),
        )

    def _tools_for_role(self, role: RoleSpec, round_index: int) -> list[str]:
        """Return the tool allowlist for this role and revision round."""

        if round_index > 0 and role.revision_allowed_tools is not None:
            return role.revision_allowed_tools
        return role.allowed_tools

    def _role_registry(self, role: RoleSpec, round_index: int):
        """Expose only the role allowlist while reusing concrete tool objects."""

        registry = ToolRegistry()
        for tool_name in self._tools_for_role(role, round_index):
            tool = self.registry.get(tool_name)
            if tool is not None:
                registry.register(tool)
        return registry

    def _build_role_task(self, role: RoleSpec, round_index: int) -> str:
        """Create the role prompt from task, role policy, and artifact handoff."""

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
        """Parse simple decision markers from review/verifier outputs."""

        if final_answer.startswith("blocked:"):
            return "BLOCKED"
        if self._looks_like_unfinished_tool_output(final_answer):
            return "NEEDS_REVISION"
        text = (final_answer or "").strip().upper()
        first_line = text.splitlines()[0].strip("*#:- `") if text else ""
        for marker in role.pass_markers:
            if first_line.startswith(marker):
                return "PASS"
        for marker in role.revision_markers:
            if first_line.startswith(marker):
                return "NEEDS_REVISION"
        for marker in role.blocked_markers:
            if first_line.startswith(marker):
                return "BLOCKED"
        if role.name in {*self.profile.review_roles, *self.profile.verifier_roles}:
            return "NEEDS_REVISION"
        return "COMPLETED"

    def _looks_like_unfinished_tool_output(self, final_answer: str) -> bool:
        """Detect provider-specific tool markup that leaked into final output."""

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

    def _trace(self, event_type: str, success: bool = True, **kwargs) -> None:
        """Emit coordinator-level trace events with monotonic synthetic steps."""

        self._event_step += 1
        agent_name = kwargs.pop("agent_name", "MultiAgentCoordinator")
        self.trace.add(self._event_step, agent_name, event_type, success=success, **kwargs)
