import unittest

from agent_forge.runtime.domain.conversation import Observation
from agent_forge.tools.registry import ToolRegistry
from agent_forge.tools.tool_router import ToolRouter


class DummyTool:
    name = "dummy"

    def schema(self):
        return {"name": self.name, "arguments": {"path": "str"}}

    def execute(self, arguments):
        return Observation(self.name, True, arguments["path"])


class ToolRegistryRouterTest(unittest.TestCase):
    def test_registry_validates_missing_arguments(self):
        registry = ToolRegistry()
        registry.register(DummyTool())
        observation = registry.execute("dummy", {})
        self.assertFalse(observation.success)
        self.assertIn("missing path", observation.content)

    def test_router_respects_read_only_task(self):
        schemas = [
            {"name": "read_file", "arguments": {"path": "str"}},
            {"name": "apply_patch", "arguments": {"path": "str", "old": "str", "new": "str"}},
            {"name": "run_command", "arguments": {"command": "str"}},
        ]
        route = ToolRouter().route("只读阅读这个仓库，不要修改文件", schemas, step=1, agent_name="Reviewer")
        self.assertIn("read_file", route.allowed_names)
        self.assertNotIn("apply_patch", route.allowed_names)
        self.assertNotIn("run_command", route.allowed_names)

    def test_router_keeps_write_tools_for_coding_task_that_only_forbids_test_edits(self):
        schemas = [
            {"name": "read_file", "arguments": {"path": "str"}},
            {"name": "apply_patch", "arguments": {"path": "str", "old": "str", "new": "str"}},
            {"name": "write_file", "arguments": {"path": "str", "content": "str"}},
            {"name": "run_command", "arguments": {"command": "str"}},
            {"name": "git_diff", "arguments": {}},
        ]
        task = "\n".join(
            [
                "You are Implementer, the coding implementer.",
                "Original task: Resolve this coding issue.",
                "Role instructions: make the smallest safe code change. Do not edit tests unless explicitly asked.",
                "Allowed role tools: read_file, apply_patch, write_file, run_command, git_diff",
            ]
        )
        route = ToolRouter().route(task, schemas, step=4, agent_name="Implementer")
        self.assertIn("apply_patch", route.allowed_names)
        self.assertIn("write_file", route.allowed_names)
        self.assertIn("run_command", route.allowed_names)
        self.assertIn("git_diff", route.allowed_names)

    def test_router_prefers_diagnostics_over_shell_commands_for_swebench(self):
        schemas = [
            {"name": "read_file", "arguments": {"path": "str"}},
            {"name": "apply_patch", "arguments": {"path": "str", "old": "str", "new": "str"}},
            {"name": "write_file", "arguments": {"path": "str", "content": "str"}},
            {"name": "run_command", "arguments": {"command": "str"}},
            {"name": "diagnostics", "arguments": {"kind": "str", "target": "str"}},
            {"name": "git_diff", "arguments": {}},
        ]
        route = ToolRouter().route("Resolve this SWE-bench coding issue.", schemas, step=4, agent_name="Implementer")
        self.assertIn("apply_patch", route.allowed_names)
        self.assertIn("diagnostics", route.allowed_names)
        self.assertNotIn("write_file", route.allowed_names)
        self.assertNotIn("run_command", route.allowed_names)


if __name__ == "__main__":
    unittest.main()
