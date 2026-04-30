from dataclasses import dataclass, asdict
from typing import Any

@dataclass
class TraceEvent:
    run_id:str
    step:int
    agent_name:str
    event_type:str
    success:bool=True
    error:str=""
    data:dict[str,Any]|None=None
    def to_dict(self):
        d=asdict(self)
        if d["data"] is None: d["data"]={}
        return d
