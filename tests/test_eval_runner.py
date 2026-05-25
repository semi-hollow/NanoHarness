import unittest, subprocess, sys
from pathlib import Path
class T(unittest.TestCase):
    def test_eval(self):
        subprocess.run([sys.executable,'-m','agent_forge.eval.eval_runner'],check=True)
        txt=Path('.agent_forge/eval_report.md').read_text(encoding='utf-8')
        case_count=sum(1 for p in Path('eval_cases').iterdir() if p.is_dir() and (p/'verify.py').exists())
        self.assertIn('case_001_single_agent_fix_test',txt)
        self.assertIn('case_005_human_approval_required',txt)
        self.assertIn('case_020_webhook_idempotency_fix',txt)
        self.assertIn(f'|total_cases|{case_count}|',txt)
        self.assertIn('agent_steps_count',txt)
        self.assertIn('trace_event_count',txt)
