class ToolRegistry:
    def __init__(self): self.tools={}
    def register(self,t): self.tools[t.name]=t
    def get(self,name): return self.tools.get(name)
