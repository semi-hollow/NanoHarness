from pathlib import Path
from .base import BaseTool
class WriteFileTool(BaseTool):
    name="write_file"; description="write file"; schema={"path":"str","content":"str"}
    def __init__(self,root): self.root=Path(root)
    def execute(self,path,content):
        p=self.root/path; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(content,encoding='utf-8'); return "written"
