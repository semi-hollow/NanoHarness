from dataclasses import dataclass
@dataclass
class GuardrailResult:
    passed:bool
    reason:str
    severity:str="low"

def input_guardrail(task:str):
    bad=["删除","rm -rf","secret","外网","http://","https://"]
    if any(b in task for b in bad): return GuardrailResult(False,"risky input","high")
    return GuardrailResult(True,"ok")
