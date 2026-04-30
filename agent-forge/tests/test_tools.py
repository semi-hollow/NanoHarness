import unittest, tempfile
from pathlib import Path
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.read_file import ReadFileTool
from agent_forge.tools.list_files import ListFilesTool
from agent_forge.tools.grep import GrepTool
from agent_forge.tools.grep import GrepSearchTool
from agent_forge.tools.apply_patch import ApplyPatchTool
from agent_forge.tools.run_command import RunCommandTool
from agent_forge.tools.registry import ToolRegistry
from agent_forge.tools.write_file import WriteFileTool
from agent_forge.tools.ask_human import AskHumanTool
from agent_forge.tools.git_status import GitStatusTool
from agent_forge.tools.git_diff import GitDiffTool
class TestTools(unittest.TestCase):
  def test_toolset(self):
    with tempfile.TemporaryDirectory() as d:
      p=Path(d)/'a.py'; p.write_text('x=1\nreturn a - b\nreturn a - b\n',encoding='utf-8')
      s=WorkspaceSandbox(d)
      self.assertTrue(ReadFileTool(s).execute({'path':'a.py'}).success)
      self.assertIn('a.py',ListFilesTool(s).execute({'path':'.'}).content)
      self.assertIn('a.py',GrepTool(s).execute({'keyword':'x=1'}).content)
      self.assertIn('a.py',GrepSearchTool(s).execute({'keyword':'x=1'}).content)
      obs=ApplyPatchTool(s).execute({'path':'a.py','old':'return a - b','new':'return a + b'})
      self.assertTrue(obs.success)
      self.assertEqual((Path(d)/'a.py').read_text().count('return a + b'),1)
      self.assertFalse(ApplyPatchTool(s).execute({'path':'a.py','old':'nope','new':'x'}).success)
      self.assertTrue(RunCommandTool(s).execute({'command':'python3.11 -m unittest'}).success)
      self.assertFalse(RunCommandTool(s).execute({'command':'rm -rf /tmp/x'}).success)
      for tool in [ReadFileTool(s), ListFilesTool(s), GrepTool(s), GrepSearchTool(s), ApplyPatchTool(s), RunCommandTool(s), WriteFileTool(s), AskHumanTool(), GitStatusTool(s), GitDiffTool(s)]:
        schema=tool.schema()
        self.assertIn('name',schema)
        self.assertIn('description',schema)
        self.assertIn('arguments',schema)
      self.assertIn('git', GitStatusTool(s).execute({}).content.lower())
      self.assertTrue(AskHumanTool(auto=True).execute({'question':'ok?'}).success)
      self.assertFalse(AskHumanTool(auto=False).execute({'question':'ok?'}).success)


  def test_unittest_discover_path_sandbox(self):
    with tempfile.TemporaryDirectory() as d:
      s=WorkspaceSandbox(d)
      tool=RunCommandTool(s)
      obs=tool.execute({'command':'python -m unittest discover ../../etc'})
      self.assertFalse(obs.success)

  def test_registry_validates_arguments(self):
    with tempfile.TemporaryDirectory() as d:
      s=WorkspaceSandbox(d)
      r=ToolRegistry()
      r.register(ReadFileTool(s))
      obs=r.execute('read_file',{})
      self.assertFalse(obs.success)
      self.assertIn('invalid arguments',obs.content)
