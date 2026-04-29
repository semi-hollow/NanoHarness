from dataclasses import dataclass

@dataclass
class RuntimeConfig:
    workspace:str
    max_steps:int=12
    auto_approve_writes:bool=True
    trace_file:str="agent_forge_trace.json"
