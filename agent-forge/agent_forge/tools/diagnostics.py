import py_compile
import subprocess
import sys

from agent_forge.runtime.observation import Observation

from .base import Tool


class DiagnosticsTool(Tool):
    """Run lightweight code diagnostics inside the workspace.

    This is the project-level stand-in for LSP diagnostics. It gives the agent
    structured validation beyond "run all tests": compile a file/tree, run
    unittest discovery, and report failures as observations.
    """

    name = "diagnostics"
    description = "run python compile or unittest diagnostics"

    def __init__(self, sandbox):
        """Store sandbox so target paths cannot escape the workspace."""

        self.sandbox = sandbox

    def schema(self):
        """Expose optional target and kind arguments to the LLM."""

        return {
            "name": self.name,
            "description": self.description,
            "arguments": {"kind": "str", "target": "str"},
            "required": ["kind"],
        }

    def execute(self, arguments):
        """Run compile or unittest diagnostics and return concise evidence."""

        kind = arguments.get("kind", "compile")
        target = arguments.get("target", ".")
        if kind == "compile":
            return self._compile(target)
        if kind == "unittest":
            return self._unittest(target)
        return Observation(self.name, False, f"unknown diagnostics kind: {kind}")

    def _compile(self, target: str) -> Observation:
        """Compile a Python file or every Python file under a directory."""

        root = self.sandbox.ensure_safe_path(target)
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        errors = []
        for path in files:
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as exc:
                rel = path.relative_to(self.sandbox.workspace_root).as_posix()
                errors.append(f"{rel}: {exc.msg}")
        if errors:
            return Observation(self.name, False, "\n".join(errors[:20]))
        return Observation(self.name, True, f"compile ok: {len(files)} python files")

    def _unittest(self, target: str) -> Observation:
        """Run unittest discovery for a workspace-relative test directory."""

        self.sandbox.ensure_safe_path(target)
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", target],
            cwd=str(self.sandbox.workspace_root),
            text=True,
            capture_output=True,
            timeout=30,
        )
        output = (proc.stdout + proc.stderr).strip()[:3000]
        return Observation(self.name, proc.returncode == 0, f"exit_code={proc.returncode}\n{output}")
