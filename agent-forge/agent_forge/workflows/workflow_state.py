from dataclasses import dataclass, field

@dataclass
class WorkflowState:
    task:str
    stage:str="plan"
    notes:list[str]=field(default_factory=list)
