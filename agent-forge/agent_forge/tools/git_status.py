from .run_command import RunCommandTool
class GitStatusTool(RunCommandTool):
    name='git_status'
    def execute(self,arguments): return super().execute({'command':'git status'})
