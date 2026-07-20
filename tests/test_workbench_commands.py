import unittest
from unittest.mock import patch

from agent_forge.workbench.presentation import commands


class WorkbenchCommandPlatformTest(unittest.TestCase):
    def test_verify_uses_powershell_on_windows(self) -> None:
        with patch.object(commands.sys, "platform", "win32"):
            command = commands.build_workbench_command("verify", {})

        self.assertEqual(
            command.command,
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                "scripts/verify.ps1",
            ],
        )

    def test_verify_keeps_bash_entrypoint_on_non_windows(self) -> None:
        with patch.object(commands.sys, "platform", "darwin"):
            command = commands.build_workbench_command("verify", {})

        self.assertEqual(command.command, ["bash", "scripts/verify.sh"])


if __name__ == "__main__":
    unittest.main()
