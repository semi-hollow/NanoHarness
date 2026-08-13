import tempfile
import unittest

from agent_forge.runtime.domain.conversation import Observation
from agent_forge.runtime.wiring import ToolRegistryBuildRequest, build_registry
from agent_forge.tools.registry import ToolRegistry
from agent_forge.tools.tool_router import ToolRouter, ToolRoutingRequest


class DummyTool:
    name = "dummy"

    def schema(self):
        return {"name": self.name, "arguments": {"path": "str"}}

    def execute(self, arguments):
        return Observation(self.name, True, arguments["path"])


class ToolRegistryRouterTest(unittest.TestCase):
    def test_default_registry_has_one_keyword_search_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = build_registry(
                ToolRegistryBuildRequest(workspace=tmp, auto=True)
            )

            self.assertIsNone(registry.get("grep"))
            self.assertIsNotNone(registry.get("grep_search"))
            self.assertIsNotNone(registry.get("find_files"))
            self.assertEqual(
                [
                    schema["name"]
                    for schema in registry.schemas()
                    if schema["name"] == "grep_search"
                ],
                ["grep_search"],
            )

    def test_registry_validates_missing_arguments(self):
        registry = ToolRegistry()
        registry.register(DummyTool())
        observation = registry.execute("dummy", {})
        self.assertFalse(observation.success)
        self.assertIn("missing path", observation.content)

    def test_router_respects_read_only_task(self):
        schemas = [
            {"name": "read_file", "arguments": {"path": "str"}},
            {
                "name": "replace_text",
                "arguments": {"path": "str", "old": "str", "new": "str"},
            },
            {"name": "run_command", "arguments": {"command": "str"}},
        ]
        route = ToolRouter().route(
            ToolRoutingRequest(
                task="只读阅读这个仓库，不要修改文件",
                schemas=schemas,
                step=1,
                agent_name="Reviewer",
            )
        )
        self.assertIn("read_file", route.allowed_names)
        self.assertNotIn("replace_text", route.allowed_names)
        self.assertNotIn("run_command", route.allowed_names)

    def test_router_keeps_write_tools_for_coding_task_that_only_forbids_test_edits(
        self,
    ):
        schemas = [
            {"name": "read_file", "arguments": {"path": "str"}},
            {
                "name": "replace_text",
                "arguments": {"path": "str", "old": "str", "new": "str"},
            },
            {"name": "create_file", "arguments": {"path": "str", "content": "str"}},
            {"name": "write_file", "arguments": {"path": "str", "content": "str"}},
            {"name": "run_command", "arguments": {"command": "str"}},
            {"name": "git_diff", "arguments": {}},
        ]
        task = "\n".join(
            [
                "You are Implementer, the coding implementer.",
                "Original task: Resolve this coding issue.",
                "Role instructions: make the smallest safe code change. Do not edit tests unless explicitly asked.",
                "Allowed role tools: read_file, replace_text, write_file, run_command, git_diff",
            ]
        )
        route = ToolRouter().route(
            ToolRoutingRequest(
                task=task,
                schemas=schemas,
                step=4,
                agent_name="Implementer",
            )
        )
        self.assertIn("replace_text", route.allowed_names)
        self.assertIn("write_file", route.allowed_names)
        self.assertIn("run_command", route.allowed_names)
        self.assertIn("git_diff", route.allowed_names)

    def test_complex_repair_task_does_not_treat_test_protection_as_global_read_only(
        self,
    ):
        schemas = [
            {"name": "read_file", "arguments": {"path": "str"}},
            {
                "name": "replace_text",
                "arguments": {"path": "str", "old": "str", "new": "str"},
            },
            {
                "name": "python_validation",
                "arguments": {"check_type": "str", "validation_target": "str"},
            },
            {"name": "ask_human", "arguments": {"question": "str"}},
        ]
        route = ToolRouter().route(
            ToolRoutingRequest(
                task=(
                    "Repair the settlement service. Do not modify tests. "
                    "Run the focused tests before finishing."
                ),
                schemas=schemas,
                step=1,
                agent_name="CodingAgent",
            )
        )

        self.assertIn("replace_text", route.allowed_names)
        self.assertIn("python_validation", route.allowed_names)

    def test_test_and_source_write_ban_remains_global_read_only(self):
        schemas = [
            {"name": "read_file", "arguments": {"path": "str"}},
            {"name": "replace_text", "arguments": {"path": "str"}},
        ]
        route = ToolRouter().route(
            ToolRoutingRequest(
                task="Review only. Do not modify tests or source files.",
                schemas=schemas,
            )
        )

        self.assertNotIn("replace_text", route.allowed_names)

    def test_skill_tool_preferences_cannot_override_read_only_policy(self):
        schemas = [
            {"name": "read_file", "arguments": {"path": "str"}},
            {"name": "replace_text", "arguments": {"path": "str"}},
            {"name": "run_command", "arguments": {"command": "str"}},
        ]

        route = ToolRouter().route(
            ToolRoutingRequest(
                task="只读分析这个问题，不要修改文件",
                schemas=schemas,
                skill_tool_names={"read_file", "replace_text", "run_command"},
            )
        )

        self.assertEqual(route.allowed_names, {"read_file"})

    def test_router_uses_keyword_search_and_allowlisted_validation_fallback_for_swebench(
        self,
    ):
        schemas = [
            {"name": "list_files", "arguments": {"path": "str"}},
            {
                "name": "find_files",
                "arguments": {"pattern": "str", "path": "str"},
            },
            {"name": "read_file", "arguments": {"path": "str"}},
            {"name": "grep_search", "arguments": {"pattern": "str"}},
            {
                "name": "replace_text",
                "arguments": {"path": "str", "old": "str", "new": "str"},
            },
            {"name": "create_file", "arguments": {"path": "str", "content": "str"}},
            {"name": "write_file", "arguments": {"path": "str", "content": "str"}},
            {"name": "run_command", "arguments": {"command": "str"}},
            {
                "name": "python_validation",
                "arguments": {
                    "check_type": "str",
                    "validation_target": "str",
                },
            },
            {"name": "git_diff", "arguments": {}},
        ]
        route = ToolRouter().route(
            ToolRoutingRequest(
                task="Resolve this SWE-bench coding issue.",
                schemas=schemas,
                step=4,
                agent_name="Implementer",
            )
        )
        self.assertIn("replace_text", route.allowed_names)
        self.assertIn("create_file", route.allowed_names)
        self.assertIn("python_validation", route.allowed_names)
        self.assertNotIn("write_file", route.allowed_names)
        self.assertIn("run_command", route.allowed_names)
        self.assertIn("grep_search", route.allowed_names)
        self.assertIn("find_files", route.allowed_names)
        self.assertNotIn("list_files", route.allowed_names)
        self.assertIn(
            "swebench_validation=python_validation|allowlisted_run_command",
            route.reason,
        )

    def test_swebench_work_phase_keeps_discovery_before_closeout(self):
        schemas = [
            {"name": "list_files"},
            {"name": "find_files"},
            {"name": "read_file"},
            {"name": "grep_search"},
            {"name": "replace_text"},
            {"name": "create_file"},
            {"name": "python_validation"},
            {"name": "run_command"},
            {"name": "git_status"},
            {"name": "git_diff"},
            {"name": "ask_human"},
        ]

        route = ToolRouter().route(
            ToolRoutingRequest(
                task="Resolve this SWE-bench coding issue.",
                schemas=schemas,
                step=14,
                max_steps=16,
            )
        )

        self.assertNotIn("list_files", route.allowed_names)
        self.assertIn("find_files", route.allowed_names)
        self.assertIn("grep_search", route.allowed_names)
        self.assertIn("ask_human", route.allowed_names)
        self.assertIn("read_file", route.allowed_names)
        self.assertIn("replace_text", route.allowed_names)
        self.assertIn("python_validation", route.allowed_names)
        self.assertEqual(route.phase, "work")

    def test_last_swebench_tool_turn_after_write_focuses_on_closure(self):
        schemas = [
            {"name": "list_files"},
            {"name": "find_files"},
            {"name": "read_file"},
            {"name": "grep_search"},
            {"name": "replace_text"},
            {"name": "create_file"},
            {"name": "python_validation"},
            {"name": "run_command"},
            {"name": "git_diff"},
        ]

        route = ToolRouter().route(
            ToolRoutingRequest(
                task="Resolve this SWE-bench coding issue.",
                schemas=schemas,
                step=15,
                max_steps=16,
            )
        )

        self.assertEqual(
            route.allowed_names,
            {
                "read_file",
                "grep_search",
                "replace_text",
                "create_file",
                "python_validation",
                "run_command",
                "git_diff",
            },
        )
        self.assertIn("closure_phase=repair_closeout", route.reason)
        self.assertEqual(route.phase, "closeout")

    def test_final_turn_is_empty_even_in_all_mode(self):
        schemas = [
            {"name": "read_file"},
            {"name": "grep_search"},
            {"name": "replace_text"},
            {"name": "python_validation"},
            {"name": "git_diff"},
        ]

        route = ToolRouter().route(
            ToolRoutingRequest(
                task="Resolve this SWE-bench coding issue.",
                schemas=schemas,
                step=16,
                max_steps=16,
                mode="all",
            )
        )

        self.assertEqual(route.allowed_names, set())
        self.assertEqual(route.schemas, [])
        self.assertEqual(route.phase, "finalize")
        self.assertEqual(route.dropped_names, sorted(item["name"] for item in schemas))


if __name__ == "__main__":
    unittest.main()
