"""收集一次执行 workspace 的 candidate diff、changed files 与可见 status。

系统角色：把 tracked 与 untracked 修改统一投影为可集成 Candidate Patch，同时排除
``.agent_forge`` 自身运行数据。
输入：Git workspace；输出：binary-safe diff、文件列表或 porcelain status。
相邻边界：ExecutionEnvironment 决定 workspace；本 Adapter 只读取 Git state。

折叠导航：1 candidate diff；2 changed/status；3 Git helper。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

RESERVED_UNTRACKED_ROOTS = {".agent_forge"}


# region 1. Candidate diff：tracked + untracked 合成一个可审计 Patch
def collect_workspace_diff(workspace: str | Path) -> str:
    root = Path(workspace).resolve()
    tracked_diff_text = _tracked_diff(root)
    untracked_file_diff_fragments: list[str] = []
    # Git diff 不包含 untracked；逐文件用 no-index 生成同格式 fragment。
    for untracked_file_path in _untracked_files(root):
        git_diff_process = subprocess.run(
            [
                "git",
                "diff",
                "--no-index",
                "--no-ext-diff",
                "--binary",
                "--",
                os.devnull,
                untracked_file_path,
            ],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if git_diff_process.returncode not in {0, 1}:
            failure_detail = (
                git_diff_process.stderr or git_diff_process.stdout
            ).strip()
            raise RuntimeError(
                f"could not capture untracked file {untracked_file_path}: "
                f"{failure_detail}"
            )
        if git_diff_process.stdout:
            untracked_file_diff_fragments.append(git_diff_process.stdout)
    return _join_diff_fragments([tracked_diff_text, *untracked_file_diff_fragments])
# endregion 1. Candidate diff 结束


# region 2. Changed files / status：隐藏运行数据，不隐藏真实代码修改
def collect_changed_files(workspace: str | Path) -> list[str]:
    root = Path(workspace).resolve()
    tracked_file_names = _git_name_list(
        root,
        ["diff", "HEAD", "--name-only", "-z", "--", "."],
    )
    # 尚无 HEAD 的仓库回退 working-tree diff，保证新仓库仍可列出修改。
    if tracked_file_names is None:
        tracked_file_names = (
            _git_name_list(
                root,
                ["diff", "--name-only", "-z", "--", "."],
            )
            or []
        )
    return sorted(set([*tracked_file_names, *_untracked_files(root)]))


def collect_workspace_status(workspace: str | Path) -> list[str]:
    root = Path(workspace).resolve()
    git_status_process = _run_git(
        root,
        ["status", "--porcelain", "--untracked-files=all"],
    )
    if git_status_process.returncode != 0:
        return []
    visible_status_lines = []
    for status_line in git_status_process.stdout.splitlines():
        changed_file_path = (
            status_line[3:].strip().strip('"') if len(status_line) > 3 else ""
        )
        if status_line.startswith("?? ") and _is_reserved_untracked(changed_file_path):
            continue
        visible_status_lines.append(status_line)
    return visible_status_lines
# endregion 2. Changed files / status 结束


# region 3. Git subprocess 与文本 helper
def _tracked_diff(root: Path) -> str:
    git_diff_process = _run_git(
        root,
        ["diff", "--no-ext-diff", "--binary", "HEAD", "--", "."],
    )
    if git_diff_process.returncode != 0:
        git_diff_process = _run_git(
            root,
            ["diff", "--no-ext-diff", "--binary", "--", "."],
        )
    return git_diff_process.stdout if git_diff_process.returncode == 0 else ""


def _untracked_files(root: Path) -> list[str]:
    names = _git_name_list(
        root, ["ls-files", "--others", "--exclude-standard", "-z", "--", "."]
    )
    return sorted(name for name in names or [] if not _is_reserved_untracked(name))


def _is_reserved_untracked(path: str) -> bool:
    first = Path(path).parts[0] if Path(path).parts else ""
    return first in RESERVED_UNTRACKED_ROOTS


def _git_name_list(root: Path, args: list[str]) -> list[str] | None:
    git_process = _run_git(root, args)
    if git_process.returncode != 0:
        return None
    return [name for name in git_process.stdout.split("\0") if name]


def _run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _join_diff_fragments(diff_fragments: list[str]) -> str:
    normalized_diff_fragments = [
        fragment if fragment.endswith("\n") else f"{fragment}\n"
        for fragment in diff_fragments
        if fragment
    ]
    return "".join(normalized_diff_fragments)
# endregion 3. Git helper 结束
