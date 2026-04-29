from dataclasses import dataclass
@dataclass
class Handoff:
    from_agent:str;to_agent:str;reason:str;payload:dict
