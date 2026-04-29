from dataclasses import dataclass
@dataclass
class EvalResult:
 case_id:str;passed:bool;task_success:bool;test_pass:bool;safety_violation:bool;handoff_count:int;tool_call_count:int;steps_count:int;notes:str
