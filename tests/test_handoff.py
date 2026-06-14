import json
import tempfile
import unittest
from pathlib import Path
from agent_forge.agents.handoff import Handoff
from agent_forge.agents.supervisor_agent import SupervisorAgent
from agent_forge.agents.supervisor_phase import TaskPhase
from agent_forge.cli import build_registry
from agent_forge.observability.trace import TraceRecorder
class T(unittest.TestCase):
    def test_h(self):
        h=Handoff('a','b','r',{})
        self.assertEqual(h.to_agent,'b')

    def test_supervisor_handoff_payload(self):
        with tempfile.TemporaryDirectory() as d:
            trace=TraceRecorder(str(Path(d)/'trace.json'))
            out=SupervisorAgent().run(trace,'fix',build_registry('.',True))
            trace.write()
            self.assertIn('ReviewerAgent',out)
            data=json.loads((Path(d)/'trace.json').read_text(encoding='utf-8'))
            handoffs=[e for e in data['events'] if e['event_type']=='handoff']
            self.assertGreaterEqual(len(handoffs),4)
            payload=handoffs[0]['handoff']['payload']
            self.assertEqual(payload['phase'],TaskPhase.PLANNING.value)
            self.assertIn('task',payload)
            self.assertIn('relevant_files',payload)
