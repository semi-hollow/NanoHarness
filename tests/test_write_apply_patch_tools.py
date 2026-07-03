import tempfile
import unittest
from pathlib import Path

from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.apply_patch import ApplyPatchTool
from agent_forge.tools.registry import ToolRegistry
from agent_forge.tools.write_file import WriteFileTool


class WriteApplyPatchToolsTest(unittest.TestCase):
    def test_apply_patch_requires_exactly_one_old_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "a.txt"
            path.write_text("same\nsame\n", encoding="utf-8")
            tool = ApplyPatchTool(WorkspaceSandbox(root))
            ambiguous = tool.execute({"path": "a.txt", "old": "same", "new": "changed"})
            self.assertFalse(ambiguous.success)
            self.assertIn("ambiguous", ambiguous.content)
            missing = tool.execute({"path": "a.txt", "old": "missing", "new": "changed"})
            self.assertFalse(missing.success)
            self.assertIn("old text not found", missing.content)
            ok = tool.execute({"path": "a.txt", "old": "same\nsame\n", "new": "changed\n"})
            self.assertTrue(ok.success, ok.content)
            self.assertEqual(path.read_text(encoding="utf-8"), "changed\n")

    def test_apply_patch_rejects_overlapping_old_text_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "a.txt"
            path.write_text("aaa", encoding="utf-8")
            tool = ApplyPatchTool(WorkspaceSandbox(root))
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
