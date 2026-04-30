from .handoff import Handoff
from .planner_agent import PlannerAgent
from .coding_agent import CodingAgent
from .tester_agent import TesterAgent
from .reviewer_agent import ReviewerAgent


class SupervisorAgent:
    def run(self, trace, task: str, registry):
        state = {"task": task, "trace": trace, "registry": registry, "retry_count": 0}
        lines = []

        # Planner -> Coding -> Tester
        for i, agent in enumerate([PlannerAgent(), CodingAgent(), TesterAgent()], 1):
            trace.add(i, "SupervisorAgent", "handoff", handoff=Handoff("SupervisorAgent", agent.name, f"stage:{agent.name}", {"task": task}).__dict__)
            lines.append(f"SupervisorAgent -> {agent.name}")
            result = agent.run(state)
            state[agent.name] = result.output

        # retry once if test failed
        if not state.get("test_pass"):
            state["retry_count"] = 1
            trace.add(4, "SupervisorAgent", "handoff", handoff=Handoff("SupervisorAgent", "CodingAgent", "retry_once", {"reason": "test_fail"}).__dict__)
            lines.append("SupervisorAgent -> CodingAgent (retry)")
            state["CodingAgent_retry"] = CodingAgent().run(state).output
            trace.add(5, "SupervisorAgent", "handoff", handoff=Handoff("SupervisorAgent", "TesterAgent", "retest", {}).__dict__)
            lines.append("SupervisorAgent -> TesterAgent (retest)")
            state["TesterAgent_retry"] = TesterAgent().run(state).output

        trace.add(6, "SupervisorAgent", "handoff", handoff=Handoff("SupervisorAgent", "ReviewerAgent", "review", {}).__dict__)
        lines.append("SupervisorAgent -> ReviewerAgent")
        state["ReviewerAgent"] = ReviewerAgent().run(state).output

        final = "pass" if state.get("test_pass") else "fail"
        lines.append(f"Final: {final}; review={state.get('review', '')}; retry={state.get('retry_count',0)}")
        return "\n".join(lines)
