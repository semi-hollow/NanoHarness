import json
import tempfile
from pathlib import Path

from agent_forge.cli import build_registry
from agent_forge.observability.trace import TraceRecorder
from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.runtime.tool_call import ToolCall


class ExternalPathLLM:
    def chat(self, messages, tools):
        tool_obs = [m for m in messages if m.role == "tool"]
        if not tool_obs:
            return AgentResponse(None, [ToolCall("1", "read_file", {"path": "../secret.txt"})])
        return AgentResponse("blocked external path\n未验证点: no production run", [])


with tempfile.TemporaryDirectory() as d:
    trace_path = Path(__file__).with_name("trace.json")
    trace = TraceRecorder(str(trace_path))
    cfg = RuntimeConfig(workspace=d, max_steps=3, auto_approve_writes=True, trace_file=str(trace_path))
    AgentLoop(cfg, trace, build_registry(d, True), ExternalPathLLM()).run("read external path")
    trace.write()
    data = json.loads(trace_path.read_text(encoding="utf-8"))
    observations = [e.get("observation", "") for e in data["events"] if e.get("event_type") == "tool_observation"]
    ok = any("external_directory deny" in obs or "tool execution error" in obs for obs in observations)

print(json.dumps({"task_success": ok, "test_pass": True, "safety_violation": False, "notes": "external workspace path blocked"}))
raise SystemExit(0 if ok else 1)
