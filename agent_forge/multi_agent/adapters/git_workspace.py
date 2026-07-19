"""Fanout 集成 workspace 的 Git adapter。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_forge.runtime.git_workspace import (
    collect_workspace_diff,
    collect_workspace_status,
)


class GitFanoutWorkspace:
    """封装主 workspace 的 patch 检查、合并和状态读取。"""

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

    def apply_patch(
        self,
        patch: str,
        *,
        check_only: bool,
    ) -> tuple[bool, str]:
        command = ["git", "apply", "--binary"]
        if check_only:
            command.append("--check")
        result = subprocess.run(
            command,
            cwd=self.workspace,
            input=patch,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        return result.returncode == 0, (result.stderr or result.stdout).strip()


def apply_patch_to_workspace(
    workspace: Path,
    patch: str,
    *,
    check_only: bool,
) -> tuple[bool, str]:
    """Worker adapter 在临时 worktree 中应用 patch。"""

    return GitFanoutWorkspace(workspace).apply_patch(patch, check_only=check_only)


def commit_worker_baseline(workspace: Path) -> None:
    """将已集成 patch 固化为 worker 的只读基线。"""

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
