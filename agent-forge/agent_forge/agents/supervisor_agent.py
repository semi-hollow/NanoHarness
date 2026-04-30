from .handoff import Handoff
from .planner_agent import PlannerAgent
from .coding_agent import CodingAgent
from .tester_agent import TesterAgent
from .reviewer_agent import ReviewerAgent


class SupervisorAgent:
    def run(self, trace, task: str):
        state={"task":task}
        agents=[PlannerAgent(),CodingAgent(),TesterAgent(),ReviewerAgent()]
        for i,a in enumerate(agents,1):
            trace.add(i,"SupervisorAgent","handoff",handoff=Handoff("SupervisorAgent",a.name,f"stage:{a.name}",state).__dict__)
            result=a.run(state)
            state[a.name]=result.output
        return "SupervisorAgent -> PlannerAgent\nSupervisorAgent -> CodingAgent\nSupervisorAgent -> TesterAgent\nSupervisorAgent -> ReviewerAgent\nFinal"
