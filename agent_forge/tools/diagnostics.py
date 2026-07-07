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
        """Run unittest diagnostics for a directory or a single test file.

        Real models often ask diagnostics to run one specific test file after a
        patch. Supporting that here keeps validation inside the diagnostics tool
        instead of pushing the model toward blocked shell commands.
        """

        command = self._unittest_command(target)
        if isinstance(command, Observation):
            return command
        proc = subprocess.run(
            command,
            cwd=str(self.sandbox.workspace_root),
            text=True,
            capture_output=True,
            timeout=30,
        )
        output = (proc.stdout + proc.stderr).strip()[:3000]
        if "ModuleNotFoundError: No module named 'pytest'" in output:
            return Observation(
                self.name,
                True,
                "validation_blocked: pytest is not installed in this benchmark workspace; "
                "candidate patch remains unverified by focused tests.",
            )
        return Observation(self.name, proc.returncode == 0, f"exit_code={proc.returncode}\n{output}")

    def _unittest_command(self, target: str) -> list[str] | Observation:
        """Normalize common LLM target forms into one unittest command.

        Models naturally alternate between file paths
        (``astropy/modeling/tests/test_x.py``), path-like module stems
        (``astropy/modeling/tests/test_x``), and dotted module names
        (``astropy.modeling.tests.test_x``). Without this normalization the
        agent wastes steps on validation syntax instead of the code fix itself.
        """

        target = (target or ".").strip() or "."
        if "/" not in target and "\\" not in target and "." in target and not target.startswith(".") and not target.endswith(".py"):
            return [sys.executable, "-m", "unittest", target]

        resolved = self.sandbox.ensure_safe_path(target)
        if not resolved.exists() and not str(resolved).endswith(".py"):
            py_candidate = self.sandbox.ensure_safe_path(f"{target}.py")
            if py_candidate.exists():
                resolved = py_candidate
        if resolved.is_file():
            return [sys.executable, str(resolved)]
        return [sys.executable, "-m", "unittest", "discover", str(resolved)]
