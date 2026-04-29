from .base import BaseTool
class AskHumanTool(BaseTool):
    name="ask_human";description="mock approval";schema={"question":"str"}
    def __init__(self,auto=True): self.auto=auto
    def execute(self,question): return "approved" if self.auto else "rejected"
