import tempfile
import unittest
from pathlib import Path

from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.grep import GrepSearchTool
from agent_forge.tools.list_files import ListFilesTool


class RepositoryDiscoveryToolsTest(unittest.TestCase):
    def test_tools_see_a_workspace_nested_under_agent_forge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".agent_forge" / "runs" / "case" / "workspace"
            source = workspace / "frontend" / "handler.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export const settlementTotal = 40;\n", encoding="utf-8")
            sandbox = WorkspaceSandbox(workspace)

            listed = ListFilesTool(sandbox).execute({})
            searched = GrepSearchTool(sandbox).execute(
                {"keyword": "settlementtotal"}
            )

        self.assertTrue(listed.success, listed.content)
        self.assertIn("frontend/handler.ts", listed.content)
        self.assertTrue(searched.success, searched.content)
        self.assertIn("frontend/handler.ts:1", searched.content)
        self.assertIn("case_sensitive=false", searched.content)

    def test_grep_reports_result_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "module.py").write_text(
                "\n".join(["needle", "needle", "needle"]),
                encoding="utf-8",
            )
            tool = GrepSearchTool(WorkspaceSandbox(workspace))

            observation = tool.execute({"keyword": "needle", "max_results": 2})

        self.assertTrue(observation.success, observation.content)
        self.assertIn("matches=2", observation.content)
        self.assertIn("truncated=true", observation.content)
        self.assertIn("next=use a narrower path", observation.content)


if __name__ == "__main__":
    unittest.main()
