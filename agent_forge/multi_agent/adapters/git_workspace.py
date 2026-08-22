"""Fanout candidate 与集成 workspace 之间的 Git Adapter。

系统角色：读取主 workspace 的 HEAD/status/diff，并用 ``git apply`` 做 check/apply。
Worker worktree 的基线固化也复用这里的 helper；本文件不决定集成顺序或冲突恢复。

折叠导航：1 主 workspace Port；2 Worker worktree helper。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_forge.runtime.adapters.git_workspace import (
    collect_workspace_diff,
    collect_workspace_status,
)
from agent_forge.multi_agent.ports import FanoutWorkspacePort


# region 1. 主 workspace Port：Coordinator 唯一通过这里读取和应用 candidate Diff
class GitFanoutWorkspace(FanoutWorkspacePort):
    """封装主 workspace 的 unified diff 检查、合并和状态读取。"""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()

    def head(self) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def status(self) -> str:
        return "\n".join(collect_workspace_status(self.workspace))

    def diff(self) -> str:
        return collect_workspace_diff(self.workspace)

    def apply_unified_diff(
        self,
        diff_text: str,
        *,
        check_only: bool,
    ) -> tuple[bool, str]:
        """把 ``git diff`` 文本交给 ``git apply`` 检查或应用。"""

        command = ["git", "apply", "--binary"]
        if check_only:
            command.append("--check")
        result = subprocess.run(
            command,
            cwd=self.workspace,
            input=diff_text,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        return result.returncode == 0, (result.stderr or result.stdout).strip()
# endregion 1. 主 workspace Port 结束


# region 2. Worker worktree helper：seed 已集成 Diff，并提交成下一 Worker 的干净基线
def apply_unified_diff_to_workspace(
    workspace: Path,
    diff_text: str,
    *,
    check_only: bool,
) -> tuple[bool, str]:
    """Worker adapter 在临时 worktree 中应用 unified diff。"""

    return GitFanoutWorkspace(workspace).apply_unified_diff(
        diff_text,
        check_only=check_only,
    )


def commit_worker_baseline(workspace: Path) -> None:
    """将已集成 diff 固化为 worker 的只读基线。"""

    subprocess.run(
        ["git", "add", "-A"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=NanoHarness",
            "-c",
            "user.email=agent-forge@local",
            "commit",
            "-m",
            "fanout integrated baseline",
        ],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
# endregion 2. Worker worktree helper 结束
