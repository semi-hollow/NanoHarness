from pathlib import Path
import unittest, tempfile, json
from agent_forge.observability.trace import TraceRecorder
from agent_forge.observability.metrics import summarize
class T(unittest.TestCase):
    def test_trace_file(self):
        with tempfile.TemporaryDirectory() as d:
            t=TraceRecorder(f'{d}/x.json'); t.set_run_context(task='task'); t.add(1,'a','llm_call',llm_request_summary='m=1',llm_response_summary='ok'); t.write()
            data=json.loads((Path(d)/'x.json').read_text())
            self.assertTrue(data['events'])
            self.assertEqual(data['task'],'task')
            event=data['events'][0]
            self.assertIn('run_id',event)
            self.assertIn('duration_ms',event)
            self.assertTrue((Path(d)/'summary.md').exists())

    def test_metrics_extra_counts(self):
        metrics=summarize([
            {'event_type':'permission_check','permission_decision':'deny'},
            {'event_type':'error'},
            {'event_type':'tool_call','tool_call':'run_command'},
        ])
        self.assertEqual(metrics['permission_denied_count'],1)
        self.assertEqual(metrics['error_count'],1)
        self.assertEqual(metrics['test_command_count'],1)
