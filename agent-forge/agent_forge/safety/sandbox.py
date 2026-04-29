from pathlib import Path
SENSITIVE=[".env","id_rsa",".pem",".key","credentials","secrets"]
class WorkspaceSandbox:
    def __init__(self,root:str): self.root=Path(root).resolve()
    def check_path(self,path:str):
        p=(self.root/path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        if not str(p).startswith(str(self.root)): return False,"external_directory deny"
        t=p.name.lower()
        if any(x in t for x in SENSITIVE): return False,"sensitive file deny"
        return True,"allow"
