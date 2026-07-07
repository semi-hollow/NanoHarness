import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_forge.ui import _build_swebench_command, _render_evidence_html


class PublicCliSmokeTest(unittest.TestCase):
    """Keep only the user-facing smoke check in the repo.

    The project effect proof is SWE-bench, not a large author-created unit-test
    suite. This test only protects the public entrypoint from obvious import or
    argparse breakage.
    """

    def test_public_cli_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent_forge", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bench", result.stdout)
        self.assertIn("ui", result.stdout)

    def test_ui_swebench_command_includes_agent_mode_defaults(self):
        command = _build_swebench_command(sys.executable, {}, regression=False)
        self.assertIn("--agent-mode", command.command)
        self.assertIn("compare", command.command)
        self.assertIn("--profile", command.command)
        self.assertIn("coding_fix", command.command)
        self.assertIn("--max-revision-rounds", command.command)
        self.assertIn("2", command.command)

    def test_interview_evidence_view_renders_without_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = _render_evidence_html(Path(tmp), "interview")
        self.assertIn("Interview Evidence", html)
        self.assertIn("5 分钟 Demo", html)
        self.assertIn("Golden Demo Capsule", html)
        self.assertIn("30 分钟学习路径", html)
        self.assertIn("docs/technical-defense/learn/三十分钟面试准备包.md", html)
        self.assertIn("Single vs Multi", html)
        self.assertIn("Safety", html)


if __name__ == "__main__":
    unittest.main()
