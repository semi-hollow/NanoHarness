from .handoff import Handoff
from .planner_agent import PlannerAgent
from .coding_agent import CodingAgent
from .tester_agent import TesterAgent
from .reviewer_agent import ReviewerAgent


class SupervisorAgent:
    def run(self, trace, task: str, registry):
        state = {"task": task, "trace": trace, "registry": registry}
        agents = [PlannerAgent(), CodingAgent(), TesterAgent(), ReviewerAgent()]
        lines = []
        for i, agent in enumerate(agents, 1):
            trace.add(i, "SupervisorAgent", "handoff", handoff=Handoff("SupervisorAgent", agent.name, f"stage:{agent.name}", {"task": task}).__dict__)
            lines.append(f"SupervisorAgent -> {agent.name}")
            result = agent.run(state)
            state[agent.name] = result.output
        final = "pass" if state.get("test_pass") else "fail"
        lines.append(f"Final: {final}; review={state.get('review','')}" )
        return "\n".join(lines)
