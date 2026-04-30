from .base import Tool
from agent_forge.runtime.observation import Observation
class GrepTool(Tool):
    name='grep'; description='keyword search'
    def __init__(self,sandbox): self.sandbox=sandbox
    def schema(self): return {"name":self.name,"description":self.description,"arguments":{"keyword":"str"}}
    def execute(self,arguments):
        kw=arguments['keyword']; out=[]
        for p in self.sandbox.workspace_root.rglob('*.py'):
            if '.git' in p.parts: continue
            txt=p.read_text(encoding='utf-8',errors='ignore')
            for i,l in enumerate(txt.splitlines(),1):
                if kw in l:
                    out.append(f"{p.relative_to(self.sandbox.workspace_root)}:{i}:{l.strip()}")
                if len(out)>=50: break
        return Observation(self.name,True,"\n".join(out))


class GrepSearchTool(GrepTool):
    name = "grep_search"
    description = "keyword or simple substring search"
