import subprocess
from .base import BaseTool
class RunCommandTool(BaseTool):
    name="run_command"; description="run cmd"; schema={"command":"str"}
    def __init__(self,root): self.root=root
    def execute(self,command):
        p=subprocess.run(command,shell=True,cwd=self.root,text=True,capture_output=True)
        return (p.stdout+p.stderr).strip()
