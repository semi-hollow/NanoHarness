import json
import shutil
import subprocess
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
        self.assertEqual(env.redact("Authorization: Bearer abcdefghijklmnop"), "Authorization: Bearer [redacted]")

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
