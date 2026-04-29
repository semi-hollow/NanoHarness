from pathlib import Path
from .base import BaseTool
class ListFilesTool(BaseTool):
    name="list_files"; description="list files"; schema={"path":"str"}
    def __init__(self,root): self.root=Path(root)
    def execute(self,path="."):
        p=(self.root/path).resolve()
        return "
".join(sorted(str(x.relative_to(self.root)) for x in p.rglob('*') if x.is_file()))
