import argparse
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_forge.forge_cli import prepare_execution_environment


class ExecutionEnvironmentCliTest(unittest.TestCase):
    def test_prepare_worktree_environment_writes_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
            (repo / "README.md").write_text("hello\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
            (repo / "README.md").write_text("local uncommitted change\n", encoding="utf-8")
            run_dir = root / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            args = argparse.Namespace(
                workspace=str(repo),
                execution_mode="worktree",
                network_policy="deny",
                keep_worktree=False,
            )

            environment, probe = prepare_execution_environment(args, "run-1", run_dir)

            self.assertEqual(probe.mode, "worktree")
            self.assertTrue(probe.dirty)
            self.assertIn("README.md", probe.dirty_files)
            self.assertNotEqual(environment.active_workspace, repo.resolve())
            self.assertTrue((run_dir / "execution_environment.json").exists())
            manifest = json.loads((run_dir / "execution_environment.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["probe"]["network_policy"], "deny")
            self.assertEqual(manifest["cleanup_policy"], "remove")
            environment.cleanup()
            self.assertFalse(environment.active_workspace.exists())


if __name__ == "__main__":
    unittest.main()
