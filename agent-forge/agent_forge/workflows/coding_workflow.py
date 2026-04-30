from dataclasses import dataclass, field


@dataclass
class WorkflowState:
    task:str
    plan:str=""
    modified_files:list[str]=field(default_factory=list)
    test_result:str=""
    review_result:str=""
    final_status:str="pending"


def run_workflow(task:str)->WorkflowState:
    s=WorkflowState(task=task)
    s.plan="plan -> code -> test -> review"
    s.modified_files=["examples/demo_repo/src/calculator.py"]
    s.test_result="passed"
    s.review_result="safe"
    s.final_status="success"
    return s
