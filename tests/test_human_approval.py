import tempfile
import unittest
from pathlib import Path

from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.apply_patch import ApplyPatchTool


class TestHumanApproval(unittest.TestCase):
    def test_auto_approve_patch(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.py"
            path.write_text("return a - b\n", encoding="utf-8")
            obs = ApplyPatchTool(WorkspaceSandbox(d), auto_approve_writes=True).execute({
                "path": "a.py",
                "old": "return a - b",
                "new": "return a + b",
            })
            self.assertTrue(obs.success)
            self.assertIn("return a + b", path.read_text(encoding="utf-8"))

    def test_no_auto_approve_rejects_patch(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.py"
            path.write_text("return a - b\n", encoding="utf-8")
            obs = ApplyPatchTool(WorkspaceSandbox(d), auto_approve_writes=False).execute({
                "path": "a.py",
                "old": "return a - b",
                "new": "return a + b",
            })
            self.assertFalse(obs.success)
            self.assertIn("needs_approval", obs.content)
            self.assertIn("return a - b", path.read_text(encoding="utf-8"))
