import shlex
import subprocess
import sys

from agent_forge.runtime.observation import Observation
from agent_forge.safety.permission import PermissionPolicy, PermissionDecision
from .base import Tool


class RunCommandTool(Tool):
    name = "run_command"
    description = "safe run command"

    def __init__(self, sandbox, auto_approve_writes=True):
        self.sandbox = sandbox
        self.policy = PermissionPolicy(auto_approve_writes)

    def schema(self):
        return {"name": self.name, "arguments": {"command": "str"}}

    def _normalize_python(self, parts: list[str]) -> list[str]:
        if parts and parts[0] == "python":
            return [sys.executable] + parts[1:]
        return parts

    def _validate_unittest_discover_path(self, parts: list[str]):
        normalized = ["python" if x == sys.executable else x for x in parts]
        if normalized[:4] == ["python", "-m", "unittest", "discover"] and len(normalized) >= 5:
            candidate = normalized[4]
            if not candidate.startswith("-"):
                self.sandbox.ensure_safe_path(candidate)

    def execute(self, arguments):
        cmd = arguments.get("command", "")
        decision, reason = self.policy.decide("run_command", cmd)
        if decision != PermissionDecision.ALLOW:
            return Observation(self.name, False, reason)

        try:
            parts = shlex.split(cmd)
            parts = self._normalize_python(parts)
            self._validate_unittest_discover_path(parts)
            proc = subprocess.run(
                parts,
                cwd=str(self.sandbox.workspace_root),
                shell=False,
                text=True,
                capture_output=True,
                timeout=20,
            )
            output = (proc.stdout + proc.stderr).strip()[:2000]
            return Observation(self.name, proc.returncode == 0, f"exit_code={proc.returncode}\n{output}")
        except Exception as e:
            return Observation(self.name, False, f"command execution error: {e}")
