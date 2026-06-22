import subprocess
import sys
import unittest


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


if __name__ == "__main__":
    unittest.main()
