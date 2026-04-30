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
