from agent_forge.runtime.observation import Observation
from agent_forge.safety.permission import PermissionPolicy, PermissionDecision
from .base import Tool

class WriteFileTool(Tool):
    name="write_file"; description="write file"
    def __init__(self, sandbox, auto_approve_writes=True): self.sandbox=sandbox; self.policy=PermissionPolicy(auto_approve_writes); self.auto=auto_approve_writes
    def schema(self): return {"name":self.name,"description":self.description,"arguments":{"path":"str","content":"str"}}
    def execute(self, arguments):
        d,r=self.policy.decide("write")
        if d==PermissionDecision.DENY: return Observation(self.name,False,r)
        if d==PermissionDecision.ASK and not self.auto: return Observation(self.name,False,"needs_approval")
        p=self.sandbox.ensure_safe_path(arguments["path"]); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(arguments["content"],encoding="utf-8")
        return Observation(self.name,True,f"written: {arguments['path']}")
