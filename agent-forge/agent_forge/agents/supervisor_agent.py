from .handoff import Handoff


class SupervisorAgent:
    def run(self, trace):
        steps = [
            ("SupervisorAgent", "PlannerAgent", "plan"),
            ("SupervisorAgent", "CodingAgent", "code"),
            ("SupervisorAgent", "TesterAgent", "test"),
            ("SupervisorAgent", "ReviewerAgent", "review"),
        ]
        out = []
        for i, (a, b, reason) in enumerate(steps):
            h = Handoff(a, b, reason, {"stage": reason})
            trace.add(step=i, agent_name=a, event_type="handoff", data=h.__dict__)
            out.append(f"{a}->{b}:{reason}")
        return "\n".join(out) + "\nfinal: done"
