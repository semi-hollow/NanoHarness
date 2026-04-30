import unittest, tempfile, json
from agent_forge.observability.trace import TraceRecorder
class T(unittest.TestCase):
    def test_trace_file(self):
        with tempfile.TemporaryDirectory() as d:
            t=TraceRecorder(f'{d}/x.json'); t.add(1,'a','llm_call'); t.write()
            self.assertTrue(json.loads(open(f'{d}/x.json').read())['events'])
