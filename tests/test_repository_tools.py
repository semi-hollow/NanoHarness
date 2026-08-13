import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.find_files import FindFilesTool
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
            searched = GrepSearchTool(sandbox).execute({"keyword": "settlementtotal"})

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

    def test_grep_reports_no_match_and_rejects_unsafe_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            (workspace / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            tool = GrepSearchTool(WorkspaceSandbox(workspace))

            no_match = tool.execute({"keyword": "missing"})
            unsafe = tool.execute({"keyword": "VALUE", "path": "../outside"})

        self.assertTrue(no_match.success, no_match.content)
        self.assertIn("matches=0", no_match.content)
        self.assertFalse(unsafe.success)

    def test_grep_reports_rg_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = GrepSearchTool(WorkspaceSandbox(tmp))
            with patch("agent_forge.tools.grep.shutil.which", return_value=None):
                observation = tool.execute({"keyword": "needle"})

        self.assertFalse(observation.success)
        self.assertIn("ripgrep", observation.content)

    def test_find_files_uses_glob_and_reports_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "src").mkdir()
            (workspace / "src" / "alpha.py").write_text("", encoding="utf-8")
            (workspace / "src" / "beta.py").write_text("", encoding="utf-8")
            (workspace / "src" / "notes.md").write_text("", encoding="utf-8")
            tool = FindFilesTool(WorkspaceSandbox(workspace))

            observation = tool.execute(
                {"pattern": "*.py", "path": "src", "max_results": 1}
            )

        self.assertTrue(observation.success, observation.content)
        self.assertIn("files=1", observation.content)
        self.assertIn("truncated=true", observation.content)
        self.assertIn("src/alpha.py", observation.content)
        self.assertNotIn("notes.md", observation.content)


if __name__ == "__main__":
    unittest.main()
