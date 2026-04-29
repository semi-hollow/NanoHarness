from .base import BaseTool
class ApplyPatchTool(BaseTool):
    name="apply_patch"; description="simple replace"; schema={"path":"str","old":"str","new":"str"}
    def __init__(self,root): self.root=root
    def execute(self,path,old,new):
        from pathlib import Path
        p=Path(self.root)/path
        t=p.read_text(encoding='utf-8').replace(old,new)
        p.write_text(t,encoding='utf-8')
        return "patched"
