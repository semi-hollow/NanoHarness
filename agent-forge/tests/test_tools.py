import unittest, tempfile
from pathlib import Path
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.read_file import ReadFileTool
from agent_forge.tools.list_files import ListFilesTool
from agent_forge.tools.grep import GrepTool
from agent_forge.tools.apply_patch import ApplyPatchTool
from agent_forge.tools.run_command import RunCommandTool
class TestTools(unittest.TestCase):
  def test_toolset(self):
    with tempfile.TemporaryDirectory() as d:
      p=Path(d)/'a.py'; p.write_text('x=1\nreturn a - b\nreturn a - b\n',encoding='utf-8')
      s=WorkspaceSandbox(d)
      self.assertTrue(ReadFileTool(s).execute({'path':'a.py'}).success)
      self.assertIn('a.py',ListFilesTool(s).execute({'path':'.'}).content)
      self.assertIn('a.py',GrepTool(s).execute({'keyword':'x=1'}).content)
      obs=ApplyPatchTool(s).execute({'path':'a.py','old':'return a - b','new':'return a + b'})
      self.assertTrue(obs.success)
      self.assertEqual((Path(d)/'a.py').read_text().count('return a + b'),1)
      self.assertFalse(ApplyPatchTool(s).execute({'path':'a.py','old':'nope','new':'x'}).success)
      self.assertTrue(RunCommandTool(s).execute({'command':'python -m unittest'}).success)
      self.assertFalse(RunCommandTool(s).execute({'command':'rm -rf /tmp/x'}).success)
