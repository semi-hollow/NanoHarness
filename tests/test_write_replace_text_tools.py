import tempfile
import unittest
from pathlib import Path

from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.create_file import CreateFileTool
from agent_forge.tools.replace_text import ReplaceTextTool
from agent_forge.tools.registry import ToolRegistry
from agent_forge.tools.write_file import WriteFileTool


class WriteReplaceTextToolsTest(unittest.TestCase):
    def test_create_file_creates_new_path_but_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = CreateFileTool(WorkspaceSandbox(root))

            created = tool.execute(
                {"path": "package/new_module.py", "content": "VALUE = 1\n"}
            )
            self.assertTrue(created.success, created.content)
            target = root / "package/new_module.py"
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 1\n")

            duplicate = tool.execute(
                {"path": "package/new_module.py", "content": "VALUE = 2\n"}
            )
            self.assertFalse(duplicate.success)
            self.assertIn("already exists", duplicate.content)
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_create_file_keeps_workspace_and_sensitive_path_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = CreateFileTool(WorkspaceSandbox(tmp))

            with self.assertRaises(PermissionError):
                tool.execute({"path": "../escape.py", "content": "bad\n"})
            with self.assertRaises(PermissionError):
                tool.execute({"path": ".env", "content": "SECRET=1\n"})

    def test_replace_text_requires_exactly_one_old_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "a.txt"
            path.write_text("same\nsame\n", encoding="utf-8")
            tool = ReplaceTextTool(WorkspaceSandbox(root))
            ambiguous = tool.execute({"path": "a.txt", "old": "same", "new": "changed"})
            self.assertFalse(ambiguous.success)
            self.assertIn("ambiguous", ambiguous.content)
            missing = tool.execute({"path": "a.txt", "old": "missing", "new": "changed"})
            self.assertFalse(missing.success)
            self.assertIn("old text not found", missing.content)
            ok = tool.execute({"path": "a.txt", "old": "same\nsame\n", "new": "changed\n"})
            self.assertTrue(ok.success, ok.content)
            self.assertEqual(path.read_text(encoding="utf-8"), "changed\n")

    def test_replace_text_rejects_overlapping_old_text_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "a.txt"
            path.write_text("aaa", encoding="utf-8")
            tool = ReplaceTextTool(WorkspaceSandbox(root))
            observation = tool.execute({"path": "a.txt", "old": "aa", "new": "bb"})
            self.assertFalse(observation.success)
            self.assertIn("ambiguous", observation.content)
            self.assertEqual(path.read_text(encoding="utf-8"), "aaa")

    def test_write_file_uses_sandbox_through_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = ToolRegistry()
            registry.register(WriteFileTool(WorkspaceSandbox(root)))
            ok = registry.execute("write_file", {"path": "notes/out.txt", "content": "hello"})
            self.assertTrue(ok.success, ok.content)
            self.assertEqual((root / "notes/out.txt").read_text(encoding="utf-8"), "hello")
            denied = registry.execute("write_file", {"path": ".env", "content": "secret"})
            self.assertFalse(denied.success)
            self.assertIn("sensitive file deny", denied.content)


if __name__ == "__main__":
    unittest.main()
