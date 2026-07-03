import unittest

from agent_forge.safety.command_policy import check_command


class CommandPolicyTest(unittest.TestCase):
    def test_blocks_dangerous_commands(self):
        for command in ["curl https://example.com", "wget http://x", "sudo ls", "git push", "git reset --hard"]:
            ok, reason = check_command(command)
            self.assertFalse(ok, command)
            self.assertTrue(reason)

    def test_allows_focused_validation_and_readonly_git(self):
        for command in [
            "python -m unittest discover tests",
            "python3 -m compileall -q agent_forge tests",
            "python3.11 -m unittest discover tests",
            "git status",
            "git diff",
            "git show HEAD",
        ]:
            ok, reason = check_command(command)
            self.assertTrue(ok, f"{command}: {reason}")


if __name__ == "__main__":
    unittest.main()
