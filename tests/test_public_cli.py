import subprocess
import sys
import unittest

from agent_forge.ui import _build_swebench_command


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
        self.assertIn("single", command.command)
        self.assertIn("--profile", command.command)
        self.assertIn("coding_fix", command.command)
        self.assertIn("--max-revision-rounds", command.command)
        self.assertIn("2", command.command)


if __name__ == "__main__":
    unittest.main()
