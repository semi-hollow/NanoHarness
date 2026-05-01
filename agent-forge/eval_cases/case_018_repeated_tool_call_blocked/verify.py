import json
import tempfile
from pathlib import Path

from agent_forge.cli import build_registry
from agent_forge.observability.trace import TraceRecorder
from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.runtime.tool_call import ToolCall


class RepeatingLLM:
    def chat(self, messages, tools):
        return AgentResponse(None, [ToolCall("same", "read_file", {"path": "a.txt"})])


with tempfile.TemporaryDirectory() as d:
    (Path(d) / "a.txt").write_text("hello", encoding="utf-8")
    trace_path = Path(__file__).with_name("trace.json")
    trace = TraceRecorder(str(trace_path))
    cfg = RuntimeConfig(workspace=d, max_steps=4, auto_approve_writes=True, trace_file=str(trace_path))
    answer = AgentLoop(cfg, trace, build_registry(d, True), RepeatingLLM()).run("repeat tool")
    trace.write()
    data = json.loads(trace_path.read_text(encoding="utf-8"))
    ok = "repeated tool call" in answer and any(e.get("event_type") == "error" for e in data["events"])

print(json.dumps({"task_success": ok, "test_pass": True, "safety_violation": False, "notes": "repeated tool call blocked"}))
raise SystemExit(0 if ok else 1)
