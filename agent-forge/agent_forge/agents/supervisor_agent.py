"""Teaching implementation of supervised multi-agent orchestration.

This module is intentionally simple and linear. It is not claiming that a
production multi-agent system should be hard-coded as planner -> coder ->
tester -> reviewer. The goal here is narrower: show the minimum moving parts
behind supervised handoff, shared state, retry after failed tests, review gate,
and traceability.

Important boundary for readers:

* ``SupervisorAgent`` is a supervisor demo, not a general scheduler.
* ``PlannerAgent``/``CodingAgent``/``TesterAgent``/``ReviewerAgent`` are role
  objects with ``run(state)`` methods, not autonomous AgentLoop instances.
* There is no parallel execution, DAG scheduling, conflict resolution, or
  per-agent context policy yet.

Production evolution would turn the supervisor into a task scheduler and run
each subagent through a shared AgentLoop/AgentRuntime with role-specific prompts,
tool permissions, context retrieval, and stop conditions.
"""

from .handoff import Handoff
from .planner_agent import PlannerAgent
from .coding_agent import CodingAgent
from .tester_agent import TesterAgent
from .reviewer_agent import ReviewerAgent
from .supervisor_phase import TaskPhase
from .supervisor_policy import SupervisorPolicy


class SupervisorAgent:
    """Coordinate demo subagents through explicit phases and handoffs.

    The class exists to make multi-agent concepts concrete without hiding them
    behind a framework. Read it as a small state-machine example, not as the
    final architecture for industrial multi-agent coding.
    """

    def __init__(self, policy: SupervisorPolicy | None = None):
        """Allow tests to inject policy; default keeps one retry for demo clarity."""

        self.policy = policy or SupervisorPolicy(max_retry=1)

    def _payload(self, phase: TaskPhase, state: dict) -> dict:
        """Build the auditable handoff payload written into trace events."""

        return {
            "phase": phase.value,
            "task": state.get("task", ""),
            "relevant_files": state.get("relevant_files", ["examples/demo_repo/src/calculator.py", "examples/demo_repo/tests/test_calculator.py"]),
            "modified_files": state.get("modified_files", []),
            "test_result": state.get("test_result"),
            "review_result": state.get("review"),
            "retry_count": state.get("retry_count", 0),
        }

    def _handoff(self, trace, step: int, to_agent: str, reason: str, phase: TaskPhase, state: dict):
        """Record one supervisor-to-subagent transition before work starts."""

        handoff = Handoff("SupervisorAgent", to_agent, reason, self._payload(phase, state))
        trace.add(
            step,
            "SupervisorAgent",
            "handoff",
            from_agent=handoff.from_agent,
            to_agent=handoff.to_agent,
            reason=handoff.reason,
            handoff=handoff.__dict__,
        )
        return handoff

    def run(self, trace, task: str, registry):
        """Run planner, coder, tester, optional retry, and reviewer in order.

        The fixed order is a deliberate teaching trade-off. It keeps the trace
        easy to read while demonstrating the control concepts that matter:
        handoff payloads, phase transitions, test-driven retry, and review
        finalization. If this were production code, this method would likely
        build a task graph and schedule multiple AgentLoop-backed workers.
        """

        trace.set_run_context(task=task)
        state = {"task": task, "trace": trace, "registry": registry, "retry_count": 0, "phase": TaskPhase.PLANNING.value}
        lines = []
        step = 1

        # The following phases are written out explicitly so a new reader can
        # map console output and trace events back to the code. A generic
        # scheduler would be more flexible, but less useful for first-pass study.
        phase = TaskPhase.PLANNING
        self._handoff(trace, step, "PlannerAgent", "create_plan", phase, state)
        lines.append("SupervisorAgent -> PlannerAgent")
        state["PlannerAgent"] = PlannerAgent().run(state).output
        state["code_done"] = False
        step += 1

        phase = self.policy.decide_next_phase(state)
        state["phase"] = phase.value
        self._handoff(trace, step, "CodingAgent", "implement_plan", phase, state)
        lines.append("SupervisorAgent -> CodingAgent")
        state["CodingAgent"] = CodingAgent().run(state).output
        state["code_done"] = True
        step += 1

        phase = self.policy.decide_next_phase(state)
        state["phase"] = phase.value
        self._handoff(trace, step, "TesterAgent", "run_tests", phase, state)
        lines.append("SupervisorAgent -> TesterAgent")
        state["TesterAgent"] = TesterAgent().run(state).output
        step += 1

        phase = self.policy.decide_next_phase(state)
        if phase == TaskPhase.CODING:
            state["retry_count"] = 1
            state["phase"] = phase.value
            self._handoff(trace, step, "CodingAgent", "retry_after_test_fail", phase, state)
            lines.append("SupervisorAgent -> CodingAgent (retry)")
            state["CodingAgent_retry"] = CodingAgent().run(state).output
            state["code_done"] = True
            step += 1
            phase = self.policy.decide_next_phase({**state, "phase": TaskPhase.CODING.value})
            state["phase"] = phase.value
            self._handoff(trace, step, "TesterAgent", "retest_after_retry", phase, state)
            lines.append("SupervisorAgent -> TesterAgent (retest)")
            state["TesterAgent_retry"] = TesterAgent().run(state).output
            step += 1

        phase = self.policy.decide_next_phase(state)
        if phase not in {TaskPhase.REVIEWING, TaskPhase.FAILED}:
            phase = TaskPhase.REVIEWING
        state["phase"] = phase.value
        self._handoff(trace, step, "ReviewerAgent", "review_diff_and_test_result", phase, state)
        lines.append("SupervisorAgent -> ReviewerAgent")
        state["ReviewerAgent"] = ReviewerAgent().run(state).output

        final_phase = self.policy.decide_next_phase(state)
        final = "pass" if final_phase == TaskPhase.DONE else "fail"
        state["phase"] = final_phase.value
        lines.append(f"Final: {final}; review={state.get('review', '')}; retry={state.get('retry_count',0)}")
        output = "\n".join(lines)
        trace.set_run_context(stop_reason=final_phase.value, final_answer=output)
        return output
