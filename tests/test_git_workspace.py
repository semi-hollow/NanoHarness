import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_forge.runtime.execution_environment import ExecutionEnvironment, ExecutionEnvironmentConfig
from agent_forge.runtime.git_workspace import (
    collect_changed_files,
    collect_workspace_diff,
    collect_workspace_status,
)
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.git_diff import GitDiffTool


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
    (root / "tracked.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=root, check=True, capture_output=True)


class GitWorkspaceTest(unittest.TestCase):
    def test_diff_includes_tracked_untracked_text_and_binary_files_and_applies_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            clean = root / "clean"
            subprocess.run(["git", "clone", str(repo), str(clean)], check=True, capture_output=True)

            (repo / "tracked.txt").write_text("after\n", encoding="utf-8")
            (repo / "new.txt").write_text("new text\n", encoding="utf-8")
            (repo / "asset.bin").write_bytes(b"\x00\x01\x02new-binary\xff")

            patch = collect_workspace_diff(repo)

            self.assertEqual(collect_changed_files(repo), ["asset.bin", "new.txt", "tracked.txt"])
            self.assertIn("b/tracked.txt", patch)
            self.assertIn("b/new.txt", patch)
            self.assertIn("b/asset.bin", patch)
            applied = subprocess.run(
                ["git", "apply", "--binary"],
                cwd=clean,
                input=patch,
                text=True,
                capture_output=True,
            )
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertEqual((clean / "tracked.txt").read_text(encoding="utf-8"), "after\n")
            self.assertEqual((clean / "new.txt").read_text(encoding="utf-8"), "new text\n")
            self.assertEqual((clean / "asset.bin").read_bytes(), b"\x00\x01\x02new-binary\xff")

    def test_environment_and_model_visible_git_diff_share_untracked_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            (repo / "new.py").write_text("value = 1\n", encoding="utf-8")
            environment = ExecutionEnvironment(ExecutionEnvironmentConfig(workspace=str(repo)))
            environment.prepare()

            self.assertIn("b/new.py", environment.diff())
            observation = GitDiffTool(WorkspaceSandbox(repo)).execute({})
            self.assertTrue(observation.success)
            self.assertIn("b/new.py", observation.content)

    def test_untracked_runtime_artifacts_are_not_user_changes_or_candidate_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            runtime_dir = repo / ".agent_forge" / "runs" / "run-1"
            runtime_dir.mkdir(parents=True)
            (runtime_dir / "trace.json").write_text("{}\n", encoding="utf-8")

            self.assertEqual(collect_workspace_status(repo), [])
            self.assertEqual(collect_changed_files(repo), [])
            self.assertEqual(collect_workspace_diff(repo), "")
            environment = ExecutionEnvironment(ExecutionEnvironmentConfig(workspace=str(repo)))
            probe = environment.prepare()
            self.assertFalse(probe.dirty)


if __name__ == "__main__":
    unittest.main()
