"""模型可调用的唯一锚点文本替换工具。

本工具接收 ``path + old + new``，不接收也不解析 unified diff。真正的 Git diff
由运行结束后的 workspace 收口生成，并作为 candidate diff artifact 保存。
"""

import os
import shutil
import time

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.permission import PermissionDecision, PermissionPolicy
from agent_forge.safety.sandbox import WorkspaceSandbox

from .base import Tool


class ReplaceTextTool(Tool):
    """在 workspace 文件中把唯一匹配的旧文本替换一次。"""

    name = "replace_text"
    description = (
        "Replace one uniquely matched text block in a workspace file. "
        "Arguments are path, old, and new; this tool does not accept unified diff."
    )

    def __init__(
        self, sandbox: WorkspaceSandbox, auto_approve_writes: bool = True
    ) -> None:
        self.sandbox = sandbox
        self.policy = PermissionPolicy(auto_approve_writes)
        self.auto_approve_writes = auto_approve_writes

    def schema(self) -> ToolSchema:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": {"path": "str", "old": "str", "new": "str"},
        }

    def execute(self, arguments: ToolArguments) -> Observation:
        decision, reason = self.policy.decide("write")
        if decision == PermissionDecision.DENY:
            return Observation(self.name, False, reason)
        if decision == PermissionDecision.ASK and not self.auto_approve_writes:
            return Observation(self.name, False, "needs_approval")

        path = self.sandbox.ensure_safe_path(arguments["path"])
        expected_text = arguments["old"]
        replacement_text = arguments["new"]
        text = path.read_text(encoding="utf-8")
        occurrences = _count_overlapping(text, expected_text)
        if occurrences == 0:
            return Observation(self.name, False, "old text not found")
        if occurrences > 1:
            return Observation(
                self.name,
                False,
                f"old text is ambiguous: found {occurrences} occurrences; reread the file and provide a unique anchor",
            )

        path.write_text(
            text.replace(expected_text, replacement_text, 1),
            encoding="utf-8",
        )

        now = time.time() + 2
        os.utime(path, (now, now))
        cache_dir = path.parent / "__pycache__"
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
        return Observation(self.name, True, f"replaced text once: {arguments['path']}")


def _count_overlapping(text: str, needle: str) -> int:
    if needle == "":
        return 0
    count = 0
    start = 0
    while True:
        index = text.find(needle, start)
        if index == -1:
            return count
        count += 1
        start = index + 1
