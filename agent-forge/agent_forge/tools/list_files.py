from .base import Tool
from agent_forge.runtime.observation import Observation
IGNORE={'.git','__pycache__','node_modules','target','dist','build'}
class ListFilesTool(Tool):
    name='list_files'; description='list files'
    def __init__(self,sandbox): self.sandbox=sandbox
    def schema(self): return {"name":self.name,"arguments":{"path":"str"}}
    def execute(self,arguments):
        root=self.sandbox.ensure_safe_path(arguments.get('path','.'))
        res=[]
        for p in root.rglob('*'):
            if any(i in p.parts for i in IGNORE): continue
            if p.is_file(): res.append(str(p.relative_to(self.sandbox.workspace_root)))
            if len(res)>=200: break
        return Observation(self.name,True,"\n".join(res))
