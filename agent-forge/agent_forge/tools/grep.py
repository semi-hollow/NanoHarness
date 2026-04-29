from pathlib import Path
from .base import BaseTool
class GrepTool(BaseTool):
    name="grep"; description="grep keyword"; schema={"keyword":"str"}
    def __init__(self,root): self.root=Path(root)
    def execute(self,keyword):
        out=[]
        for p in self.root.rglob('*.py'):
            t=p.read_text(encoding='utf-8')
            if keyword in t: out.append(str(p.relative_to(self.root)))
        return "
".join(out)
