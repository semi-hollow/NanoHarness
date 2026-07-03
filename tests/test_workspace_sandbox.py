import tempfile
import unittest
from pathlib import Path

from agent_forge.safety.sandbox import WorkspaceSandbox


class WorkspaceSandboxTest(unittest.TestCase):
    def test_allows_workspace_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = WorkspaceSandbox(tmp)
            self.assertEqual(sandbox.ensure_safe_path("src/app.py"), Path(tmp).resolve() / "src/app.py")

    def test_blocks_external_and_sensitive_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = WorkspaceSandbox(tmp)
            with self.assertRaises(PermissionError):
                sandbox.ensure_safe_path("../outside.txt")
            with self.assertRaises(PermissionError):
                sandbox.ensure_safe_path(".env")
            with self.assertRaises(PermissionError):
                sandbox.ensure_safe_path("secrets/token.txt")

    def test_blocks_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root.parent / f"{root.name}-outside.txt"
            outside.write_text("secret", encoding="utf-8")
            try:
                (root / "link").symlink_to(outside)
                sandbox = WorkspaceSandbox(root)
                with self.assertRaises(PermissionError):
                    sandbox.ensure_safe_path("link")
            finally:
                outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
