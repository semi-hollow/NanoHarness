from .run_command import RunCommandTool
class GitDiffTool(RunCommandTool):
    name='git_diff'
    def execute(self,arguments): return super().execute({'command':'git diff'})
