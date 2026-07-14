from __future__ import annotations

import os
import subprocess
from pathlib import Path

RESERVED_UNTRACKED_ROOTS = {".agent_forge"}


def collect_workspace_diff(workspace: str | Path) -> str:

    root = Path(workspace).resolve()
    tracked = _tracked_diff(root)
    additions: list[str] = []
    for relative in _untracked_files(root):
        result = subprocess.run(
            [
                "git",
                "diff",
                "--no-index",
                "--no-ext-diff",
                "--binary",
                "--",
                os.devnull,
                relative,
            ],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode not in {0, 1}:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"could not capture untracked file {relative}: {detail}")
        if result.stdout:
            additions.append(result.stdout)
    return _join_patches([tracked, *additions])


def collect_changed_files(workspace: str | Path) -> list[str]:

    root = Path(workspace).resolve()
    tracked = _git_name_list(root, ["diff", "HEAD", "--name-only", "-z", "--", "."])
    if tracked is None:
        tracked = _git_name_list(root, ["diff", "--name-only", "-z", "--", "."]) or []
    return sorted(set([*tracked, *_untracked_files(root)]))


def collect_workspace_status(workspace: str | Path) -> list[str]:

    root = Path(workspace).resolve()
    result = _run_git(root, ["status", "--porcelain", "--untracked-files=all"])
    if result.returncode != 0:
        return []
    lines = []
    for line in result.stdout.splitlines():
        path = line[3:].strip().strip('"') if len(line) > 3 else ""
        if line.startswith("?? ") and _is_reserved_untracked(path):
            continue
        lines.append(line)
    return lines


def _tracked_diff(root: Path) -> str:
    result = _run_git(root, ["diff", "--no-ext-diff", "--binary", "HEAD", "--", "."])
    if result.returncode != 0:
        result = _run_git(root, ["diff", "--no-ext-diff", "--binary", "--", "."])
    return result.stdout if result.returncode == 0 else ""


def _untracked_files(root: Path) -> list[str]:
    names = _git_name_list(root, ["ls-files", "--others", "--exclude-standard", "-z", "--", "."])
    return sorted(name for name in names or [] if not _is_reserved_untracked(name))


def _is_reserved_untracked(path: str) -> bool:
    first = Path(path).parts[0] if Path(path).parts else ""
    return first in RESERVED_UNTRACKED_ROOTS


def _git_name_list(root: Path, args: list[str]) -> list[str] | None:
    result = _run_git(root, args)
    if result.returncode != 0:
        return None
    return [name for name in result.stdout.split("\0") if name]


def _run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _join_patches(parts: list[str]) -> str:
    normalized = [part if part.endswith("\n") else f"{part}\n" for part in parts if part]
    return "".join(normalized)
