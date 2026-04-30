from enum import Enum

from .handoff import Handoff
from .planner_agent import PlannerAgent
from .coding_agent import CodingAgent
from .tester_agent import TesterAgent
from .reviewer_agent import ReviewerAgent


class TaskPhase(Enum):
    PLANNING = "planning"
    CODING = "coding"
    TESTING = "testing"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"


class SupervisorAgent:
    def _payload(self, phase: TaskPhase, state: dict) -> dict:
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
        trace.set_run_context(task=task)
        state = {"task": task, "trace": trace, "registry": registry, "retry_count": 0, "phase": TaskPhase.PLANNING.value}
        lines = []
        step = 1

        phase = TaskPhase.PLANNING
        self._handoff(trace, step, "PlannerAgent", "create_plan", phase, state)
        lines.append("SupervisorAgent -> PlannerAgent")
        state["PlannerAgent"] = PlannerAgent().run(state).output
        step += 1

        phase = TaskPhase.CODING
        state["phase"] = phase.value
        self._handoff(trace, step, "CodingAgent", "implement_plan", phase, state)
        lines.append("SupervisorAgent -> CodingAgent")
        state["CodingAgent"] = CodingAgent().run(state).output
        step += 1

        phase = TaskPhase.TESTING
        state["phase"] = phase.value
        self._handoff(trace, step, "TesterAgent", "run_tests", phase, state)
        lines.append("SupervisorAgent -> TesterAgent")
        state["TesterAgent"] = TesterAgent().run(state).output
        step += 1

        if not state.get("test_pass"):
            state["retry_count"] = 1
            phase = TaskPhase.CODING
            state["phase"] = phase.value
            self._handoff(trace, step, "CodingAgent", "retry_after_test_fail", phase, state)
            lines.append("SupervisorAgent -> CodingAgent (retry)")
            state["CodingAgent_retry"] = CodingAgent().run(state).output
            step += 1
            phase = TaskPhase.TESTING
            state["phase"] = phase.value
            self._handoff(trace, step, "TesterAgent", "retest_after_retry", phase, state)
            lines.append("SupervisorAgent -> TesterAgent (retest)")
            state["TesterAgent_retry"] = TesterAgent().run(state).output
            step += 1

        phase = TaskPhase.REVIEWING
        state["phase"] = phase.value
        self._handoff(trace, step, "ReviewerAgent", "review_diff_and_test_result", phase, state)
        lines.append("SupervisorAgent -> ReviewerAgent")
        state["ReviewerAgent"] = ReviewerAgent().run(state).output

        final = "pass" if state.get("test_pass") else "fail"
        final_phase = TaskPhase.DONE if final == "pass" else TaskPhase.FAILED
        state["phase"] = final_phase.value
        lines.append(f"Final: {final}; review={state.get('review', '')}; retry={state.get('retry_count',0)}")
        output = "\n".join(lines)
        trace.set_run_context(stop_reason=final_phase.value, final_answer=output)
        return output
