from .run_command import RunCommandTool
class GitDiffTool(RunCommandTool):
    name="git_diff"
    def execute(self): return super().execute("git diff")
