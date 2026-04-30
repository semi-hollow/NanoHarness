from .base import Tool
from agent_forge.runtime.observation import Observation
class AskHumanTool(Tool):
    name='ask_human'; description='approval simulation'
    def __init__(self,auto=True): self.auto=auto
    def schema(self): return {"name":self.name,"arguments":{"question":"str"}}
    def execute(self,arguments):
        return Observation(self.name, self.auto, 'approved' if self.auto else 'needs_approval')
