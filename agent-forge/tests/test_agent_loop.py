import unittest, tempfile, shutil, json
from pathlib import Path
from agent_forge.cli import build_registry
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.llm_client import AgentResponse, MockLLMClient
from agent_forge.runtime.tool_call import ToolCall
from agent_forge.observability.trace import TraceRecorder


class RecordingLLM:
  def __init__(self):
    self.calls=[]

  def chat(self,messages,tools):
    self.calls.append((messages,tools))
    if len(self.calls)==1:
      return AgentResponse(None,[ToolCall('1','read_file',{'path':'examples/demo_repo/src/calculator.py'})])
    return AgentResponse('done',[])


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
      self.assertTrue({'context_assembly','plan','action','tool_call','tool_observation','observation','final_answer'}.issubset(kinds))
      self.assertTrue(Path(d,'summary.md').exists())

  def test_context_is_injected_into_llm_messages(self):
    with tempfile.TemporaryDirectory() as d:
      shutil.copytree('examples',Path(d)/'examples')
      tr=TraceRecorder(str(Path(d)/'trace.json'))
      cfg=RuntimeConfig(workspace=d,max_steps=3,auto_approve_writes=True,trace_file=str(Path(d)/'trace.json'))
      llm=RecordingLLM()
      AgentLoop(cfg,tr,build_registry(d,True),llm).run('fix calculator')
      tr.write()
      first_messages,_=llm.calls[0]
      self.assertEqual(first_messages[0].role,'system')
      content=first_messages[0].content
      self.assertIn('selected_files:',content)
      self.assertIn('available_tools:',content)
      self.assertIn('permission_summary:',content)
      self.assertIn('total_chars:',content)
      events=json.loads(Path(d,'trace.json').read_text(encoding='utf-8'))['events']
      self.assertIn('context_assembly',{e['event_type'] for e in events})
