import unittest, tempfile
from pathlib import Path
from agent_forge.safety.sandbox import WorkspaceSandbox
class TestSandbox(unittest.TestCase):
    def test_paths(self):
        with tempfile.TemporaryDirectory() as d:
            s=WorkspaceSandbox(d)
            p=s.ensure_safe_path('a.txt'); self.assertTrue(str(p).startswith(str(Path(d).resolve())))
            with self.assertRaises(PermissionError): s.ensure_safe_path('../x')
            with self.assertRaises(PermissionError): s.ensure_safe_path('.env')
            with self.assertRaises(PermissionError): s.ensure_safe_path('k.pem')

    def test_prefix_bypass_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'work'
            root.mkdir()
            evil=Path(d)/'work_evil'
            evil.mkdir()
            s=WorkspaceSandbox(root)
            with self.assertRaises(PermissionError):
                s.ensure_safe_path(evil/'x.txt')
