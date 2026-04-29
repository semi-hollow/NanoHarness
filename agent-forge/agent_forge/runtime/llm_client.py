class MockLLMClient:
    def plan_single(self,task,messages):
        step=len(messages)
        if step==0: return {"tool":"read_file","args":{"path":"examples/demo_repo/src/calculator.py"}}
        if step==1: return {"tool":"read_file","args":{"path":"examples/demo_repo/tests/test_calculator.py"}}
        if step==2: return {"tool":"apply_patch","args":{"path":"examples/demo_repo/src/calculator.py","old":"return a - b","new":"return a + b"}}
        if step==3: return {"tool":"run_command","args":{"command":"python -m unittest discover examples/demo_repo/tests"}}
        return {"final":"已完成修复并验证测试通过。"}
