import unittest, subprocess, sys
from pathlib import Path
class T(unittest.TestCase):
    def test_eval(self):
        subprocess.run([sys.executable,'-m','agent_forge.eval.eval_runner'],check=True)
        txt=Path('eval_report.md').read_text(encoding='utf-8')
        self.assertIn('case_001_single_agent_fix_test',txt)
        self.assertIn('case_005_human_approval_required',txt)
