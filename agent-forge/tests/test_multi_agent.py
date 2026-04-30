from pathlib import Path
import unittest, tempfile, json
from agent_forge.agents.supervisor_agent import SupervisorAgent
from agent_forge.observability.trace import TraceRecorder
class T(unittest.TestCase):
    def test_multi(self):
        with tempfile.TemporaryDirectory() as d:
            tr=TraceRecorder(f'{d}/t.json')
            from agent_forge.cli import build_registry
            out=SupervisorAgent().run(tr,'fix',build_registry('.',True))
            tr.write()
            self.assertIn('PlannerAgent',out)
            self.assertIn('Final: pass',out)
            events=json.loads((Path(d)/'t.json').read_text())['events']
            self.assertGreaterEqual(sum(e['event_type']=='handoff' for e in events),4)
