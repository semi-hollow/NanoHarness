import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_forge.runtime.execution_environment import (
    ExecutionEnvironment,
    ExecutionEnvironmentConfig,
)
from agent_forge.runtime.wiring import ToolRegistryBuildRequest, build_registry
from tests.support import FakeOciRunner


class ContainerExecutionEnvironmentTest(unittest.TestCase):
    def _git_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"], cwd=repo, check=True
        )
        (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        return repo

    def test_container_mode_constrains_runtime_and_records_replay_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._git_repo(root)
            runner = FakeOciRunner()
            environment = ExecutionEnvironment(
                ExecutionEnvironmentConfig(
                    mode="container",
                    workspace=str(repo),
                    run_id="run-1",
                    keep_worktree=False,
                    network_policy="deny",
                    container_runtime="docker",
                    container_image="python:3.11-slim",
                    container_cpus=1.5,
                    container_memory="512m",
                    container_pids_limit=64,
                ),
                oci_runner=runner,
                executable_resolver=lambda name: "/usr/local/bin/docker"
                if name == "docker"
                else None,
            )

            probe = environment.prepare()
            active_workspace = environment.active_workspace
            command_result = environment.execute_command(
                ["python", "-m", "unittest", "discover", "tests"],
                timeout=30,
            )
            manifest_path = environment.write_manifest(root / "artifacts")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            start = next(command for command in runner.commands if command[1] == "run")
            delegated = next(
                command for command in runner.commands if command[1] == "exec"
            )
            self.assertEqual(probe.mode, "container")
            self.assertNotEqual(active_workspace, repo.resolve())
            self.assertIn("--network", start)
            self.assertEqual(start[start.index("--network") + 1], "none")
            self.assertIn("--read-only", start)
            self.assertIn("--cap-drop", start)
            self.assertEqual(start[start.index("--cap-drop") + 1], "ALL")
            self.assertIn("no-new-privileges", start)
            self.assertIn("--env", start)
            self.assertEqual(start[start.index("--env") + 1], "HOME=/tmp")
            self.assertEqual(start[start.index("--cpus") + 1], "1.5")
            self.assertEqual(start[start.index("--memory") + 1], "512m")
            self.assertEqual(start[start.index("--pids-limit") + 1], "64")
            self.assertIn(str(active_workspace), " ".join(start))
            self.assertEqual(
                delegated[-5:], ["python", "-m", "unittest", "discover", "tests"]
            )
            self.assertEqual(command_result.returncode, 0)
            self.assertEqual(manifest["container"]["image_id"], "sha256:image-id")
            self.assertEqual(
                manifest["container"]["command_history"][0]["argv"][0], "python"
            )
            self.assertEqual(manifest["container"]["recreate_command"], [])
            self.assertFalse(manifest["container"]["replayable_after_cleanup"])

            environment.cleanup()

            self.assertFalse(active_workspace.exists())
            self.assertTrue(
                any(command[1:3] == ["rm", "-f"] for command in runner.commands)
            )

    def test_container_mode_fails_closed_when_runtime_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            environment = ExecutionEnvironment(
                ExecutionEnvironmentConfig(
                    mode="container",
                    workspace=tmp,
                    container_runtime="docker",
                    container_image="python:3.11-slim",
                ),
                executable_resolver=lambda name: None,
            )
            with self.assertRaisesRegex(RuntimeError, "container runtime"):
                environment.prepare()

    def test_container_mode_rejects_invalid_boundary_configuration(self):
        invalid_configs = [
            ({"network_policy": "sometimes"}, "network policy"),
            ({"container_cpus": 0}, "CPU limit"),
            ({"container_memory": ""}, "memory limit"),
            ({"container_pids_limit": 0}, "PID limit"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for overrides, message in invalid_configs:
                with self.subTest(overrides=overrides):
                    environment = ExecutionEnvironment(
                        ExecutionEnvironmentConfig(
                            mode="container", workspace=tmp, **overrides
                        ),
                        executable_resolver=lambda name: "/usr/local/bin/docker",
                    )
                    with self.assertRaisesRegex(ValueError, message):
                        environment.prepare()

    def test_registry_injects_same_environment_into_command_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            environment = object()
            registry = build_registry(
                ToolRegistryBuildRequest(
                    workspace=tmp,
                    auto=True,
                    execution_environment=environment,
                )
            )
        self.assertIs(registry.get("run_command").execution_environment, environment)
        self.assertIs(registry.get("diagnostics").execution_environment, environment)

    def test_local_environment_keeps_python_alias_on_active_interpreter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            environment = ExecutionEnvironment(
                ExecutionEnvironmentConfig(mode="local", workspace=tmp)
            )
            environment.prepare()
            result = environment.execute_command(["python3", "--version"], timeout=10)
            manifest_path = environment.write_manifest(root / "artifacts")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(manifest["probe"]["python_executable"], sys.executable)
        self.assertEqual(
            environment._command_history[0]["runtime_command"][0], sys.executable
        )


if __name__ == "__main__":
    unittest.main()
