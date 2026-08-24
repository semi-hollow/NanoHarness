"""每个 SWE-bench Case 的隔离 Git worktree Adapter。

系统角色：按 repo cache + base commit 创建独立 detached worktree，让 Agent 修改不污染源
仓库，并在结束时收集 Candidate Patch。
输入：``BenchCase``/variant；输出：隔离 workspace 与 diff。
相邻边界：Benchmark Application 决定何时运行/清理；本 Adapter 只管理 Git 物理边界。

折叠导航：1 prepare worktree；2 repo lock/cache；3 command；4 clean/diff/url helper。
"""

from __future__ import annotations

import fcntl
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from agent_forge.bench.domain.config import safe_id
from agent_forge.bench.domain.models import BenchCase
from agent_forge.runtime.adapters.git_workspace import collect_workspace_diff


class SwebenchWorkspaceManager:
    def __init__(self, repo_cache: Path, output_dir: Path) -> None:
        self.repo_cache = repo_cache.resolve()
        self.output_dir = output_dir.resolve()

# region 1. 准备隔离 worktree
    def prepare(self, case: BenchCase, variant: str = "") -> Path:
        _, cache_key = repo_url_and_cache_key(case.repo)
        with self._repo_lock(cache_key):
            source = self._ensure_repo(case.repo)
            suffix = f"__{safe_id(variant)}" if variant else ""
            workspace = (
                self.output_dir / "workspaces" / f"{safe_id(case.instance_id)}{suffix}"
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
            # base commit 本地不可用时只补 fetch 后重试一次，第二次失败直接上抛。
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
    # endregion 1. Prepare worktree 结束

    # region 2. Per-repo lock 与 cache
    @contextmanager
    def _repo_lock(self, cache_key: str) -> Iterator[None]:
        """只串行化同一 repo 的 clone/fetch/worktree metadata 操作。"""

        lock_root = self.repo_cache / ".locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_path = lock_root / f"{safe_id(cache_key)}.lock"
        with lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

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
    # endregion 2. Per-repo lock 与 cache 结束

# region 3. Git 命令错误边界
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
    # endregion 3. Git command error boundary 结束


# region 4. 清理、candidate diff 与仓库 URL 辅助逻辑
def ensure_clean_git(workspace: Path) -> None:
    """只用于 Benchmark 自有 worktree，恢复到 frozen base 后清除未跟踪文件。"""

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


def collect_candidate_diff(workspace: Path) -> str:
    """返回 workspace 相对基线的 unified diff 文本。"""

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
# endregion 4. Clean / diff / URL helper 结束
