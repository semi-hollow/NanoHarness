from .coding_agent import CodingAgent
from .planner_agent import PlannerAgent
from .reviewer_agent import ReviewerAgent
from .tester_agent import TesterAgent


def build_default_agents():
    """Return the default subagent map for experiments outside SupervisorAgent.

    The current SupervisorAgent instantiates role objects directly so its order
    is easy to read. This registry is kept as the extension point for a future
    scheduler that chooses agents by name or capability.
    """

    return {
        "PlannerAgent": PlannerAgent(),
        "CodingAgent": CodingAgent(),
        "TesterAgent": TesterAgent(),
        "ReviewerAgent": ReviewerAgent(),
    }
