import unittest, tempfile, json, os, shutil
from agent_forge.cli import build_registry
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.observability.trace import TraceRecorder
class T(unittest.TestCase):
  def test_single(self):
    tmp=tempfile.mkdtemp(); shutil.copytree('examples',tmp+'/examples')
    tr=TraceRecorder(tmp+'/t.json'); cfg=RuntimeConfig(workspace=tmp)
    ans=AgentLoop(cfg,tr,build_registry(tmp)).run('fix'); tr.write(); self.assertIn('完成',ans)
