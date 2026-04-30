import unittest, tempfile, shutil, json
from pathlib import Path
from agent_forge.cli import build_registry
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.llm_client import MockLLMClient
from agent_forge.observability.trace import TraceRecorder
class T(unittest.TestCase):
  def test_single(self):
    with tempfile.TemporaryDirectory() as d:
      shutil.copytree('examples',Path(d)/'examples')
      tr=TraceRecorder(str(Path(d)/'trace.json'))
      cfg=RuntimeConfig(workspace=d,max_steps=8,auto_approve_writes=True,trace_file=str(Path(d)/'trace.json'))
      ans=AgentLoop(cfg,tr,build_registry(d,True),MockLLMClient('single')).run('fix')
      tr.write()
      self.assertIn('测试通过',ans)
      self.assertIn('return a + b',(Path(d)/'examples/demo_repo/src/calculator.py').read_text())
      events=json.loads(Path(d,'trace.json').read_text())['events']
      kinds={e['event_type'] for e in events}
      self.assertTrue({'tool_call','tool_observation','final_answer'}.issubset(kinds))
