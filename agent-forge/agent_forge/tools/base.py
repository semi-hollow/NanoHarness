class BaseTool:
    name=""; description=""; schema={}
    def execute(self, **kwargs): raise NotImplementedError
