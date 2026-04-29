from pathlib import Path
from .base import BaseTool
class ReadFileTool(BaseTool):
    name="read_file"; description="read file"; schema={"path":"str"}
    def __init__(self,root): self.root=Path(root)
    def execute(self,path): return (self.root/path).read_text(encoding='utf-8')
