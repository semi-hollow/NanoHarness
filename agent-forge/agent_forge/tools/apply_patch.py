import os, time
from agent_forge.runtime.observation import Observation
from agent_forge.safety.permission import PermissionPolicy, PermissionDecision
from .base import Tool

class ApplyPatchTool(Tool):
    name="apply_patch"; description="replace once"
    def __init__(self, sandbox, auto_approve_writes=True): self.sandbox=sandbox; self.policy=PermissionPolicy(auto_approve_writes); self.auto=auto_approve_writes
    def schema(self): return {"name":self.name,"arguments":{"path":"str","old":"str","new":"str"}}
    def execute(self, arguments):
        d,r=self.policy.decide("apply_patch")
        if d==PermissionDecision.DENY: return Observation(self.name,False,r)
        if d==PermissionDecision.ASK and not self.auto: return Observation(self.name,False,"needs_approval")
        p=self.sandbox.ensure_safe_path(arguments["path"]); txt=p.read_text(encoding="utf-8")
        if arguments["old"] not in txt: return Observation(self.name,False,"old text not found")
        p.write_text(txt.replace(arguments["old"],arguments["new"],1),encoding="utf-8")
        now=time.time()+2
        os.utime(p,(now,now))
        return Observation(self.name,True,f"patched once: {arguments['path']}")
