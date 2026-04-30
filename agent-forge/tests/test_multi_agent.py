import unittest, tempfile, json
from agent_forge.agents.supervisor_agent import SupervisorAgent
from agent_forge.observability.trace import TraceRecorder
class T(unittest.TestCase):
    def test_multi(self):
        with tempfile.TemporaryDirectory() as d:
            tr=TraceRecorder(f'{d}/t.json')
            out=SupervisorAgent().run(tr,'fix')
            tr.write()
            self.assertIn('PlannerAgent',out)
            events=json.loads(open(f'{d}/t.json').read())['events']
            self.assertGreaterEqual(sum(e['event_type']=='handoff' for e in events),4)
