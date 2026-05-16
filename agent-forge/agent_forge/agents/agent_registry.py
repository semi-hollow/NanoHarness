from .coding_agent import CodingAgent
from .planner_agent import PlannerAgent
from .reviewer_agent import ReviewerAgent
from .tester_agent import TesterAgent


def build_default_agents():
    """Return the default subagent map for experiments outside SupervisorAgent."""

    return {
        "PlannerAgent": PlannerAgent(),
        "CodingAgent": CodingAgent(),
        "TesterAgent": TesterAgent(),
        "ReviewerAgent": ReviewerAgent(),
    }
