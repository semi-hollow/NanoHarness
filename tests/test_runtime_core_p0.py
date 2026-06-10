import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_forge.runtime.execution_environment import ExecutionEnvironment, ExecutionEnvironmentConfig
from agent_forge.runtime.hooks import HookContext, HookDecisionType, HookManager
from agent_forge.runtime.task_state import TaskRunStatus, TaskStateStore
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.mcp_config import MCPConfigLoader
from agent_forge.tools.registry import ToolRegistry
from agent_forge.workflows.review_workflow import run_review
from agent_forge.observability.trace import TraceRecorder


class RuntimeCoreP0Tests(unittest.TestCase):
    def test_execution_environment_blocks_network_and_redacts_keys(self):
        env = ExecutionEnvironment(ExecutionEnvironmentConfig(workspace=".", network_policy="deny"))
        ok, reason = env.validate_command("curl https://example.com")
        self.assertFalse(ok)
        self.assertIn("network command blocked", reason)
        token = "abcd" + "efghijklmnop"
        self.assertEqual(env.redact(f"Authorization: Bearer {token}"), "Authorization: Bearer [redacted]")

    def test_hook_manager_denies_environment_violation_before_permission(self):
        env = ExecutionEnvironment(ExecutionEnvironmentConfig(workspace=".", network_policy="deny"))
        hooks = HookManager.default(env)
        result = hooks.pre_tool(
            HookContext(
                run_id="r1",
                step=1,
                agent_name="CodingAgent",
                tool_name="run_command",
                arguments={"command": "curl https://example.com"},
                action="run_command",
                command="curl https://example.com",
            )
        )
        self.assertEqual(result.decision, HookDecisionType.DENY)

    def test_hook_manager_locked_mode_blocks_side_effects(self):
        env = ExecutionEnvironment(ExecutionEnvironmentConfig(workspace="."))
        hooks = HookManager.default(env, approval_mode="locked")
        result = hooks.pre_tool(
            HookContext(
                run_id="r1",
                step=1,
                agent_name="CodingAgent",
                tool_name="apply_patch",
                arguments={"path": "README.md", "old": "a", "new": "b"},
                action="apply_patch",
                approval_mode="locked",
            )
        )
        self.assertEqual(result.decision, HookDecisionType.DENY)
        self.assertIn("locked", result.reason)

    def test_execution_environment_manifest_is_auditable(self):
        with tempfile.TemporaryDirectory() as d:
            env = ExecutionEnvironment(ExecutionEnvironmentConfig(workspace=d, network_policy="deny"))
            manifest = env.write_manifest(Path(d) / "run")
            data = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(data["probe"]["active_workspace"], str(Path(d).resolve()))
            self.assertEqual(data["probe"]["network_policy"], "deny")

    def test_task_state_store_round_trips_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            store = TaskStateStore(d)
            checkpoint = store.start("run1", "task", ".", "CodingAgent")
            store.update(checkpoint, status=TaskRunStatus.RUNNING.value, current_step=2, last_tool="read_file")
            loaded = store.load("run1")
            self.assertEqual(loaded.current_step, 2)
            self.assertEqual(loaded.last_tool, "read_file")

    def test_mcp_config_loader_registers_allowlisted_tool(self):
        with tempfile.TemporaryDirectory() as d:
            config = Path(d) / "mcp.json"
            config.write_text(
                json.dumps(
                    {
                        "allowed_tools": ["local.echo"],
                        "servers": [
                            {
                                "name": "local",
                                "tools": [
                                    {
                                        "name": "echo",
                                        "handler": "echo",
                                        "input_schema": {
                                            "type": "object",
                                            "properties": {"message": {"type": "string"}},
                                            "required": ["message"],
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            registry = ToolRegistry()
            report = MCPConfigLoader(WorkspaceSandbox(d)).load_into(registry, config)
            self.assertTrue(report.tools[0].registered)
            observation = registry.execute("local.echo", {"message": "ok"})
            self.assertTrue(observation.success)
            self.assertIn("ok", observation.content)

    def test_mcp_stdio_server_discovery_and_call(self):
        with tempfile.TemporaryDirectory() as d:
            server = Path(d) / "server.py"
            server.write_text(
                """
import json
import sys

for line in sys.stdin:
    req = json.loads(line)
    method = req.get("method")
    if method == "initialize":
        result = {"protocolVersion": "test", "capabilities": {}}
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "lookup",
                    "description": "lookup test value",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            ]
        }
    elif method == "tools/call":
        args = req.get("params", {}).get("arguments", {})
        result = {"content": [{"type": "text", "text": "found:" + args.get("query", "")}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": result}), flush=True)
""",
                encoding="utf-8",
            )
            config = Path(d) / "mcp_stdio.json"
            config.write_text(
                json.dumps(
                    {
                        "allowed_tools": ["svc.lookup"],
                        "servers": [
                            {
                                "name": "svc",
                                "transport": "stdio",
                                "command": sys.executable,
                                "args": [str(server)],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            registry = ToolRegistry()
            report = MCPConfigLoader(WorkspaceSandbox(d)).load_into(registry, config)
            self.assertTrue(report.tools[0].registered)
            observation = registry.execute("svc.lookup", {"query": "agent"})
            self.assertTrue(observation.success)
            self.assertEqual(observation.content, "found:agent")

    @unittest.skipIf(shutil.which("git") is None, "git is required for review workflow")
    def test_review_workflow_flags_shell_true(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init"], cwd=d, check=True, capture_output=True)
            (root / "tool.py").write_text("print('ok')\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=d, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "init"],
                cwd=d,
                check=True,
                capture_output=True,
            )
            (root / "tool.py").write_text("import subprocess\nsubprocess.run('ls', shell=True)\n", encoding="utf-8")
            trace = TraceRecorder(str(root / "trace.json"))
            report = run_review(d, trace, "review shell change")
            self.assertEqual(report.verdict, "blocked")
            self.assertTrue(any("shell execution risk" in finding.title for finding in report.findings))


if __name__ == "__main__":
    unittest.main()
