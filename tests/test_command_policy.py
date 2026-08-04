import unittest

from agent_forge.safety.command_policy import check_command
from agent_forge.safety.guardrails import input_guardrail


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

    def test_blocks_shell_operators_even_after_allowlisted_command(self):
        for command in [
            "pytest tests 2>&1",
            "python -m pytest tests | tee result.txt",
            "git status && git show HEAD",
        ]:
            ok, reason = check_command(command)
            self.assertFalse(ok, command)
            self.assertIn("shell operators", reason)

    def test_task_text_is_not_an_execution_authorization_boundary(self):
        tasks = [
            "Read https://github.com/example/project/issues/1 and explain the bug.",
            "The report mentions rm -rf, ../, .env and id_rsa as blocked examples.",
            "修复日志中记录的删除失败，但不要执行未授权命令。",
        ]
        for task in tasks:
            result = input_guardrail(task)
            self.assertTrue(result.passed, task)
            self.assertIn("tool policy", result.reason)


if __name__ == "__main__":
    unittest.main()
