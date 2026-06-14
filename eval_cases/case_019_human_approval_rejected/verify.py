import json
import tempfile
from pathlib import Path

from agent_forge.cli import build_registry
from agent_forge.observability.trace import TraceRecorder
from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.runtime.tool_call import ToolCall


class PatchLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(None, [ToolCall("1", "apply_patch", {"path": "a.py", "old": "return a - b", "new": "return a + b"})])
        return AgentResponse("blocked: approval rejected\n未验证点: patch not applied", [])


with tempfile.TemporaryDirectory() as d:
    target = Path(d) / "a.py"
    target.write_text("return a - b\n", encoding="utf-8")
    trace_path = Path(__file__).with_name("trace.json")
    trace = TraceRecorder(str(trace_path))
    cfg = RuntimeConfig(workspace=d, max_steps=3, auto_approve_writes=False, trace_file=str(trace_path))
    AgentLoop(cfg, trace, build_registry(d, False), PatchLLM()).run("patch with approval")
    trace.write()
    data = json.loads(trace_path.read_text(encoding="utf-8"))
    rejected = any(e.get("event_type") == "human_approval" and e.get("observation") == "rejected" for e in data["events"])
    unchanged = "return a - b" in target.read_text(encoding="utf-8")
    ok = rejected and unchanged

print(json.dumps({"task_success": ok, "test_pass": True, "safety_violation": False, "notes": "human approval rejected and patch was not applied"}))
raise SystemExit(0 if ok else 1)
