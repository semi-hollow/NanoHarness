from .run_command import RunCommandTool
class GitStatusTool(RunCommandTool):
    name='git_status'
    def schema(self): return {"name":self.name,"description":"safe git status","arguments":{},"required":[]}
    def execute(self,arguments): return super().execute({'command':'git status'})
