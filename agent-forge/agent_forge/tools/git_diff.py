from .run_command import RunCommandTool
class GitDiffTool(RunCommandTool):
    name='git_diff'
    def schema(self): return {"name":self.name,"description":"safe git diff","arguments":{},"required":[]}
    def execute(self,arguments): return super().execute({'command':'git diff'})
