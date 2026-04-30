import shlex, subprocess
from pathlib import Path
from agent_forge.runtime.observation import Observation
from agent_forge.safety.permission import PermissionPolicy, PermissionDecision
from .base import Tool

class RunCommandTool(Tool):
    name="run_command"; description="safe run command"
    def __init__(self,sandbox,auto_approve_writes=True): self.sandbox=sandbox; self.policy=PermissionPolicy(auto_approve_writes)
    def schema(self): return {"name":self.name,"arguments":{"command":"str"}}

    def _validate_unittest_discover_path(self, parts: list[str]):
        if parts[:4] == ["python","-m","unittest","discover"] and len(parts) >= 5:
            candidate = parts[4]
            self.sandbox.ensure_safe_path(candidate)

    def execute(self, arguments):
        cmd=arguments.get("command","")
        d,r=self.policy.decide("run_command",cmd)
        if d!=PermissionDecision.ALLOW: return Observation(self.name,False,r)
        parts=shlex.split(cmd)
        try:
            self._validate_unittest_discover_path(parts)
        except Exception as e:
            return Observation(self.name,False,f"invalid unittest discover path: {e}")
        proc=subprocess.run(parts,cwd=str(self.sandbox.workspace_root),shell=False,text=True,capture_output=True,timeout=20)
        return Observation(self.name,proc.returncode==0,f"exit_code={proc.returncode}\n{(proc.stdout+proc.stderr).strip()[:2000]}")
