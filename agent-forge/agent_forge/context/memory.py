class Memory:
 def __init__(self,n=5): self.items=[]; self.n=n
 def add(self,x): self.items=(self.items+[x])[-self.n:]
 def recent(self): return self.items
