from .run_command import RunCommandTool
class GitStatusTool(RunCommandTool):
    name="git_status"
    def execute(self): return super().execute("git status")
