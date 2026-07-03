import unittest

from agent_forge.runtime.observation import Observation
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


if __name__ == "__main__":
    unittest.main()
