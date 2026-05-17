from dataclasses import dataclass, field


@dataclass
class WorkflowState:
    """State returned by the deterministic workflow demo.

    This object represents a fixed process result, not an agent transcript. It
    exists so readers can compare deterministic workflow state with AgentLoop's
    observation-driven state.
    """

    task: str
    plan: str = ""
    modified_files: list[str] = field(default_factory=list)
    test_result: str = ""
    review_result: str = ""
    final_status: str = "pending"


def run_workflow(task: str) -> WorkflowState:
    """Run a fixed plan-code-test-review path without LLM decisions.

    Workflow mode is intentionally simple: no context assembly, no LLM call, no
    tool registry, no retry from observations. It is the control sample for
    explaining when a normal workflow is enough and when an agent loop is
    justified.
    """

    state = WorkflowState(task=task)
    state.plan = "plan -> code -> test -> review"
    state.modified_files = ["examples/demo_repo/src/calculator.py"]
    state.test_result = "passed"
    state.review_result = "safe"
    state.final_status = "success"
    return state
