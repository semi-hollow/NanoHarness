from __future__ import annotations

import subprocess
from pathlib import Path

from agent_forge.bench.domain.config import safe_id
from agent_forge.bench.domain.models import BenchCase
from agent_forge.runtime.git_workspace import collect_workspace_diff


class SwebenchWorkspaceManager:

    def __init__(self, repo_cache: Path, output_dir: Path) -> None:
        self.repo_cache = repo_cache.resolve()
        self.output_dir = output_dir.resolve()

    def prepare(self, case: BenchCase, variant: str = "") -> Path:
        source = self._ensure_repo(case.repo)
        suffix = f"__{safe_id(variant)}" if variant else ""
        workspace = (
            self.output_dir
            / "workspaces"
            / f"{safe_id(case.instance_id)}{suffix}"
        )
        workspace.parent.mkdir(parents=True, exist_ok=True)
        self._run(["git", "-C", str(source), "worktree", "prune"], check=False)
        result = self._run(
            [
                "git",
                "-C",
                str(source),
                "worktree",
                "add",
                "--detach",
                str(workspace),
                case.base_commit,
            ],
            check=False,
        )
        if result.returncode != 0:
            self._run(
                ["git", "-C", str(source), "fetch", "origin", case.base_commit],
                check=False,
            )
            self._run(
                [
                    "git",
                    "-C",
                    str(source),
                    "worktree",
                    "add",
                    "--detach",
                    str(workspace),
                    case.base_commit,
                ],
                check=True,
            )
        return workspace

    def _ensure_repo(self, repo: str) -> Path:
        self.repo_cache.mkdir(parents=True, exist_ok=True)
        url, cache_key = repo_url_and_cache_key(repo)
        target = self.repo_cache / cache_key
        if (target / ".git").exists():
            self._run(
                ["git", "-C", str(target), "fetch", "--all", "--tags", "--prune"],
                check=False,
            )
            return target
        self._run(["git", "clone", url, str(target)], check=True)
        return target

    @staticmethod
    def _run(
        command: list[str],
        *,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(command, text=True, capture_output=True)
        if check and result.returncode != 0:
            raise RuntimeError(
                f"command failed: {' '.join(command)}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result


def ensure_clean_git(workspace: Path) -> None:

    subprocess.run(
        ["git", "-C", str(workspace), "reset", "--hard"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "clean", "-fdx"],
        check=True,
        capture_output=True,
        text=True,
    )


def collect_patch(workspace: Path) -> str:
    return collect_workspace_diff(workspace)


def repo_url_and_cache_key(repo: str) -> tuple[str, str]:
    if repo.startswith("file://"):
        local_path_text = repo.removeprefix("file://")
        return repo, f"local__{safe_id(local_path_text)}"
    local_path = Path(repo)
    if local_path.exists():
        resolved = str(local_path.resolve())
        return resolved, f"local__{safe_id(resolved)}"
    return f"https://github.com/{repo}.git", repo.replace("/", "__")
