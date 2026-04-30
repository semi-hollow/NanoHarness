import shlex, subprocess
from agent_forge.runtime.observation import Observation
from agent_forge.safety.command_policy import check_command
from .base import Tool


class RunCommandTool(Tool):
    name="run_command"; description="safe run command"
    def __init__(self, sandbox): self.sandbox=sandbox
    def schema(self): return {"name":self.name,"arguments":{"command":"str"}}
    def execute(self, arguments):
        cmd = arguments.get("command","")
        ok, reason = check_command(cmd)
        if not ok:
            return Observation(self.name, False, reason)
        parts = shlex.split(cmd)
        proc = subprocess.run(parts, cwd=str(self.sandbox.workspace_root), shell=False, text=True, capture_output=True, timeout=20)
        out = (proc.stdout + proc.stderr).strip()
        return Observation(self.name, proc.returncode==0, f"exit_code={proc.returncode}\n{out[:2000]}")
