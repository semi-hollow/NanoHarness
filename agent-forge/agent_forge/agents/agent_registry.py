from .planner_agent import PlannerAgent
from .coding_agent import CodingAgent
from .tester_agent import TesterAgent
from .reviewer_agent import ReviewerAgent

def build_default_agents():
    return {a.name:a for a in [PlannerAgent(),CodingAgent(),TesterAgent(),ReviewerAgent()]}
