import os
import shutil
import time

from agent_forge.runtime.observation import Observation
from agent_forge.safety.permission import PermissionDecision, PermissionPolicy

from .base import Tool


class ApplyPatchTool(Tool):
    """Replace one exact text block in a workspace file.

    The tool keeps edits simple and auditable: the model must name the target
    path, the exact old text, and the replacement text. Failed matches become
    observations the agent can recover from.
    """

    name = "apply_patch"
    description = "replace once"

    def __init__(self, sandbox, auto_approve_writes: bool = True):
        """Store sandbox and policy so every edit is checked first."""

        self.sandbox = sandbox
        self.policy = PermissionPolicy(auto_approve_writes)
        self.auto_approve_writes = auto_approve_writes

    def schema(self):
        """Tell the LLM this tool needs path, old text, and replacement text."""

        return {
            "name": self.name,
            "description": self.description,
            "arguments": {"path": "str", "old": "str", "new": "str"},
        }

    def execute(self, arguments):
        """Apply the edit or return a failed Observation for loop recovery."""

        decision, reason = self.policy.decide("apply_patch")
        if decision == PermissionDecision.DENY:
            return Observation(self.name, False, reason)
        if decision == PermissionDecision.ASK and not self.auto_approve_writes:
            return Observation(self.name, False, "needs_approval")

        path = self.sandbox.ensure_safe_path(arguments["path"])
        text = path.read_text(encoding="utf-8")
        if arguments["old"] not in text:
            # This failure is intentionally recoverable. AgentLoop/StepController
            # classifies it as PATCH_MISMATCH, prompting the model to reread the
            # file and repair the patch anchor.
            return Observation(self.name, False, "old text not found")

        path.write_text(text.replace(arguments["old"], arguments["new"], 1), encoding="utf-8")

        # Give file watchers and tests a visible mtime bump after fast edits.
        now = time.time() + 2
        os.utime(path, (now, now))
        cache_dir = path.parent / "__pycache__"
        if cache_dir.exists():
            # CPython can otherwise reuse stale bytecode in very fast demo runs.
            shutil.rmtree(cache_dir, ignore_errors=True)
        return Observation(self.name, True, f"patched once: {arguments['path']}")
