import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agent_forge.runtime.git_workspace import collect_workspace_diff, collect_workspace_status


NETWORK_COMMANDS = {"curl", "wget", "ssh", "scp", "nc", "telnet"}
PROTECTED_GIT_COMMANDS = {"push", "reset", "checkout", "switch", "merge", "rebase"}
PROTECTED_PATH_PARTS = {".git", ".venv", ".agent_forge"}


@dataclass(frozen=True)
class ExecutionEnvironmentConfig:
    """User-selected execution boundary for one run.

    Choose local, git-worktree, or OCI-container execution; decide whether
    network access is allowed; and decide whether the isolated snapshot remains
    available for inspection after the run.
    """

    # ``local`` runs against the current checkout. ``worktree`` creates an
    # isolated git checkout. ``container`` mounts an isolated snapshot into OCI.
    mode: str = "local"

    # Workspace requested by CLI before any worktree redirection.
    workspace: str = "."

    # Run/session id used to name isolated worktrees and audit records.
    run_id: str = ""

    # Base directory for retained worktrees.
    worktree_root: str = ".agent_forge/worktrees"

    # Network is denied by default because coding-agent tools should not fetch
    # arbitrary data unless the operator explicitly allows it.
    network_policy: str = "deny"

    # Keep worktree folders by default so a failed run can be inspected.
    keep_worktree: bool = True

    # Docker-compatible OCI CLI and a pre-pulled image used by container mode.
    container_runtime: str = "docker"
    container_image: str = "python:3.11-slim"

    # Explicit resource limits keep one agent command from monopolizing the host.
    container_cpus: float = 1.0
    container_memory: str = "1g"
    container_pids_limit: int = 256
    container_read_only: bool = True

    # Non-git workspaces use a copied snapshot under this directory.
    snapshot_root: str = ".agent_forge/snapshots"


@dataclass(frozen=True)
class EnvironmentProbe:
    """Serializable snapshot of the execution environment.

    This is deliberately verbose because execution isolation is one of the
    first production questions reviewers ask about CodingAgents.
    """

    # Active mode after prepare: local, worktree, or container.
    mode: str

    # Original user checkout path.
    requested_workspace: str

    # Actual directory where tools run.
    active_workspace: str

    # Git root for the requested workspace, if any.
    git_root: str

    # Current branch name or detached marker.
    current_branch: str

    # Current commit sha when git is available.
    head_sha: str

    # Origin remote URL, redacted if it contains credentials.
    origin_url: str

    # Whether the requested checkout had uncommitted changes at prepare time.
    dirty: bool

    # Dirty file paths at prepare/probe time, truncated for trace readability.
    dirty_files: list[str]

    # Network policy enforced by hooks/command checks.
    network_policy: str

    # Python executable used by this process.
    python_executable: str

    # Notes explain degraded behavior such as missing git.
    notes: list[str] = field(default_factory=list)

    # OCI evidence is empty for local/worktree modes.
    container_runtime: str = ""
    container_image: str = ""
    container_image_id: str = ""
    container_id: str = ""
    resource_limits: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return JSON-safe metadata for trace and task-state checkpoints."""

        return asdict(self)


class ExecutionEnvironment:
    """Execution boundary shared by command tools and runtime hooks.

    Local mode is path and command policy only. Worktree mode isolates repository
    side effects in a detached checkout. Container mode additionally delegates
    command execution to a constrained OCI container over an isolated snapshot;
    host-side file tools remain limited by the same snapshot path boundary.
    """

    def __init__(
        self,
        config: ExecutionEnvironmentConfig,
        *,
        oci_runner=None,
        executable_resolver=None,
    ):
        """Store config and resolve the requested workspace early."""

        self.config = config
        self.requested_workspace = Path(config.workspace).resolve()
        self.active_workspace = self.requested_workspace
        self.created_worktree: Path | None = None
        self.created_snapshot: Path | None = None
        self._notes: list[str] = []
        self._requested_dirty_files: list[str] | None = None
        self._oci_runner = oci_runner or subprocess.run
        self._executable_resolver = executable_resolver or shutil.which
        self._container_runtime_path = ""
        self._container_image_id = ""
        self._container_id = ""
        self._container_name = ""
        self._container_start_command: list[str] = []
        self._command_history: list[dict[str, object]] = []

    def prepare(self) -> EnvironmentProbe:
        """Prepare the active workspace and return an auditable probe.

        Worktree mode creates an isolated checkout at HEAD. It intentionally
        does not copy uncommitted local edits; that keeps runs reproducible and
        makes dirty working-tree state visible in the probe.
        """

        if self.config.mode not in {"local", "worktree", "container"}:
            raise ValueError(f"unsupported execution environment: {self.config.mode}")
        self._validate_config()

        self._requested_dirty_files = self._dirty_files(self.requested_workspace)
        if self.config.mode == "worktree":
            self._prepare_worktree()
        elif self.config.mode == "container":
            self._prepare_container()

        return self.probe()

    def probe(self) -> EnvironmentProbe:
        """Inspect git/python/network metadata without mutating the workspace."""

        git_root = self._git_output(["git", "rev-parse", "--show-toplevel"], cwd=self.active_workspace)
        branch = self._git_output(["git", "branch", "--show-current"], cwd=self.active_workspace) or "detached"
        head_sha = self._git_output(["git", "rev-parse", "HEAD"], cwd=self.active_workspace)
        origin_url = self.redact(self._git_output(["git", "remote", "get-url", "origin"], cwd=self.active_workspace))
        dirty_files = (
            list(self._requested_dirty_files)
            if self._requested_dirty_files is not None
            else self._dirty_files(self.requested_workspace)
        )
        dirty = bool(dirty_files)
        return EnvironmentProbe(
            mode=self.config.mode,
            requested_workspace=str(self.requested_workspace),
            active_workspace=str(self.active_workspace),
            git_root=git_root,
            current_branch=branch,
            head_sha=head_sha,
            origin_url=origin_url,
            dirty=dirty,
            dirty_files=dirty_files[:50],
            network_policy=self.config.network_policy,
            python_executable=sys.executable,
            notes=list(self._notes),
            container_runtime=self._container_runtime_path,
            container_image=self.config.container_image if self.config.mode == "container" else "",
            container_image_id=self._container_image_id,
            container_id=self._container_id,
            resource_limits=self._resource_limits() if self.config.mode == "container" else {},
        )

    def resolve_path(self, path: str | Path) -> Path:
        """Resolve a tool path inside the active workspace."""

        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.active_workspace / candidate
        return candidate.resolve()

    def validate_path(self, path: str | Path) -> tuple[bool, str]:
        """Check that a model-proposed path stays inside the active workspace."""

        resolved = self.resolve_path(path)
        try:
            relative = resolved.relative_to(self.active_workspace)
        except ValueError:
            return False, "path escapes execution environment"
        if any(part in PROTECTED_PATH_PARTS for part in relative.parts):
            return False, f"protected path blocked by execution environment: {resolved.name}"
        return True, "path allowed by execution environment"

    def validate_command(self, command: str) -> tuple[bool, str]:
        """Apply environment-level command checks before subprocess execution."""

        try:
            parts = shlex.split(command)
        except ValueError as exc:
            return False, f"invalid command: {exc}"
        if not parts:
            return False, "empty command"

        executable = parts[0].lower()
        if self.config.network_policy == "deny" and executable in NETWORK_COMMANDS:
            return False, f"network command blocked by execution environment: {executable}"

        if executable == "git" and len(parts) > 1:
            subcommand = parts[1].lower()
            if subcommand in PROTECTED_GIT_COMMANDS:
                return False, f"git {subcommand} blocked by execution environment"

        return True, "command allowed by execution environment"

    def redact(self, text: str) -> str:
        """Remove obvious credential material before observations enter trace."""

        if not text:
            return text
        patterns = [
            (r"Authorization:\s*Bearer\s+[A-Za-z0-9._\-]+", "Authorization: Bearer [redacted]"),
            (r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*[A-Za-z0-9._\-]{12,}", r"\1=[redacted]"),
            (r"sk-[A-Za-z0-9_\-]{16,}", "sk-[redacted]"),
            (r"Bearer\s+[A-Za-z0-9._\-]{16,}", "Bearer [redacted]"),
        ]
        redacted = text
        for pattern, replacement in patterns:
            redacted = re.sub(pattern, replacement, redacted)
        for name in ("DEEPSEEK_API_KEY", "AGENT_FORGE_API_KEY", "OPENAI_API_KEY"):
            value = os.getenv(name, "")
            if value and len(value) >= 8:
                redacted = redacted.replace(value, f"[redacted:{name}]")
        return redacted

    def cleanup(self) -> None:
        """Always remove the container; remove snapshots when configured."""

        container_target = self._container_id or self._container_name
        if container_target and self._container_runtime_path:
            removed = self._oci_runner(
                [self._container_runtime_path, "rm", "-f", container_target],
                text=True,
                capture_output=True,
                timeout=30,
            )
            if removed.returncode == 0:
                self._notes.append("removed OCI container")
            else:
                detail = self.redact((removed.stderr or removed.stdout or "unknown error").strip())
                self._notes.append(f"OCI container cleanup failed: {detail}")
            self._container_id = ""

        self._cleanup_snapshot()

    def write_manifest(self, output_dir: str | Path) -> Path:
        """Write environment metadata for session reports and run audits."""

        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        path = output / "execution_environment.json"
        manifest = {
            "probe": self.probe().to_dict(),
            "worktree_created": str(self.created_worktree or ""),
            "snapshot_created": str(self.created_snapshot or ""),
            "cleanup_policy": "keep" if self.config.keep_worktree else "remove",
        }
        if self.config.mode == "container":
            replayable_after_cleanup = bool(
                self.config.keep_worktree
                and (self.created_worktree or self.created_snapshot)
                and self._container_start_command
            )
            manifest["container"] = {
                "runtime": self.config.container_runtime,
                "runtime_path": self._container_runtime_path,
                "image": self.config.container_image,
                "image_id": self._container_image_id,
                "container_id": self._container_id,
                "network_policy": self.config.network_policy,
                "root_read_only": self.config.container_read_only,
                "resource_limits": self._resource_limits(),
                "start_command": self._container_start_command,
                "recreate_command": self._container_start_command if replayable_after_cleanup else [],
                "replayable_after_cleanup": replayable_after_cleanup,
                "exec_prefix": [self._container_runtime_path, "exec", self._container_id],
                "command_history": self._command_history,
                "boundary_note": (
                    "Commands run in the OCI container; host file tools remain constrained to the mounted snapshot."
                ),
            }
        path.write_text(
            self._json_dump(manifest),
            encoding="utf-8",
        )
        return path

    def diff(self) -> str:
        """Return git diff for the active workspace."""

        return collect_workspace_diff(self.active_workspace)

    def describe(self) -> str:
        """Human-readable environment summary for prompts and reports."""

        probe = self.probe()
        return (
            f"execution_environment mode={probe.mode}; "
            f"active_workspace={probe.active_workspace}; "
            f"network_policy={probe.network_policy}; "
            f"branch={probe.current_branch}; dirty={probe.dirty}"
        )

    def execute_command(self, argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        """Execute argv in the selected environment and record replay metadata."""

        if not argv:
            raise ValueError("empty command argv")
        started = time.monotonic()
        if self.config.mode == "container":
            if not self._container_id or not self._container_runtime_path:
                raise RuntimeError("container execution environment is not prepared")
            runtime_command = [self._container_runtime_path, "exec", self._container_id, *argv]
            result = self._oci_runner(
                runtime_command,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        else:
            normalized = (
                [sys.executable, *argv[1:]]
                if argv[0] in {"python", "python3", "python3.11"}
                else list(argv)
            )
            runtime_command = normalized
            result = subprocess.run(
                normalized,
                cwd=str(self.active_workspace),
                shell=False,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        self._command_history.append(
            {
                "argv": list(argv),
                "runtime_command": runtime_command,
                "timeout_seconds": timeout,
                "returncode": result.returncode,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "stdout_chars": len(result.stdout or ""),
                "stderr_chars": len(result.stderr or ""),
            }
        )
        return result

    def _prepare_container(self) -> None:
        """Create an isolated snapshot and start a constrained OCI container."""

        runtime = self._executable_resolver(self.config.container_runtime)
        if not runtime:
            raise RuntimeError(
                f"container runtime not found: {self.config.container_runtime}; install it or use local/worktree mode"
            )
        self._container_runtime_path = str(runtime)
        inspect = self._oci_runner(
            [self._container_runtime_path, "image", "inspect", "--format", "{{.Id}}", self.config.container_image],
            text=True,
            capture_output=True,
            timeout=30,
        )
        if inspect.returncode != 0:
            raise RuntimeError(
                f"container image is unavailable: {self.config.container_image}; pull it explicitly before the run"
            )
        self._container_image_id = (inspect.stdout or "").strip()
        self._prepare_snapshot()

        safe_run_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", self.config.run_id or uuid.uuid4().hex[:8])
        name = f"agent-forge-{safe_run_id}"[:63]
        self._container_name = name
        network = "none" if self.config.network_policy == "deny" else "bridge"
        command = [
            self._container_runtime_path,
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,src={self.active_workspace},dst=/workspace",
            "--network",
            network,
            "--cpus",
            str(self.config.container_cpus),
            "--memory",
            self.config.container_memory,
            "--pids-limit",
            str(self.config.container_pids_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--env",
            "HOME=/tmp",
        ]
        if self.config.container_read_only:
            command.append("--read-only")
        if hasattr(os, "getuid") and hasattr(os, "getgid"):
            command.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
        command.extend(
            [
                self.config.container_image,
                "sh",
                "-c",
                "while :; do sleep 3600; done",
            ]
        )
        self._container_start_command = command
        started = self._oci_runner(command, text=True, capture_output=True, timeout=60)
        if started.returncode != 0:
            self._cleanup_snapshot()
            raise RuntimeError(
                f"container start failed: {(started.stderr or started.stdout).strip()}"
            )
        self._container_id = (started.stdout or "").strip()
        if not self._container_id:
            self.cleanup()
            raise RuntimeError("container runtime returned no container id")
        self._notes.append("started constrained OCI container over isolated workspace snapshot")

    def _prepare_snapshot(self) -> None:
        """Use a detached worktree for git repos or a bounded copy otherwise."""

        git_root = self._git_output(["git", "rev-parse", "--show-toplevel"], cwd=self.requested_workspace)
        if git_root:
            self._prepare_worktree(required=True)
            return
        run_id = self.config.run_id or uuid.uuid4().hex[:8]
        target = (self.requested_workspace / self.config.snapshot_root / run_id).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target = target.with_name(f"{target.name}-{uuid.uuid4().hex[:6]}")
        shutil.copytree(
            self.requested_workspace,
            target,
            ignore=shutil.ignore_patterns(".git", ".agent_forge", ".venv", "__pycache__", "*.pyc"),
        )
        self.created_snapshot = target
        self.active_workspace = target
        self._notes.append("created isolated filesystem snapshot for non-git workspace")

    def _resource_limits(self) -> dict[str, object]:
        return {
            "cpus": self.config.container_cpus,
            "memory": self.config.container_memory,
            "pids": self.config.container_pids_limit,
        }

    def _validate_config(self) -> None:
        """Reject ambiguous or ineffective execution-boundary settings."""

        if self.config.network_policy not in {"deny", "allow"}:
            raise ValueError(f"unsupported network policy: {self.config.network_policy}")
        if self.config.mode != "container":
            return
        if self.config.container_cpus <= 0:
            raise ValueError("container CPU limit must be greater than zero")
        if not self.config.container_memory.strip():
            raise ValueError("container memory limit must not be empty")
        if self.config.container_pids_limit <= 0:
            raise ValueError("container PID limit must be greater than zero")
        if not self.config.container_runtime.strip():
            raise ValueError("container runtime must not be empty")
        if not self.config.container_image.strip():
            raise ValueError("container image must not be empty")

    def _cleanup_snapshot(self) -> None:
        """Remove an isolated snapshot when the configured retention policy allows it."""

        snapshot = self.created_worktree or self.created_snapshot
        if not snapshot or self.config.keep_worktree:
            return
        if snapshot.exists():
            shutil.rmtree(snapshot, ignore_errors=True)
        self._git_output(["git", "worktree", "prune"], cwd=self.requested_workspace)

    def _prepare_worktree(self, required: bool = False) -> None:
        """Create a detached git worktree for side-effect isolation."""

        if not (self.requested_workspace / ".git").exists():
            git_root = self._git_output(["git", "rev-parse", "--show-toplevel"])
            if not git_root:
                if required:
                    raise RuntimeError("isolated git snapshot requested but no git repository was found")
                self._notes.append("worktree requested but no git repository was found; using local mode")
                self.active_workspace = self.requested_workspace
                return

        run_id = self.config.run_id or uuid.uuid4().hex[:8]
        target = (self.requested_workspace / self.config.worktree_root / run_id).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            suffix = uuid.uuid4().hex[:6]
            target = target.with_name(f"{target.name}-{suffix}")

        result = subprocess.run(
            ["git", "worktree", "add", "--detach", str(target), "HEAD"],
            cwd=str(self.requested_workspace),
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            if required:
                raise RuntimeError(f"isolated worktree creation failed: {(result.stderr or result.stdout).strip()}")
            self._notes.append(f"worktree creation failed: {(result.stderr or result.stdout).strip()}")
            self.active_workspace = self.requested_workspace
            return

        self.created_worktree = target
        self.active_workspace = target
        self._notes.append("created isolated git worktree from HEAD")

    def _git_output(self, command: list[str], cwd: Path | None = None) -> str:
        """Run a read-only git command and return stripped stdout."""

        try:
            result = subprocess.run(
                command,
                cwd=str(cwd or self.requested_workspace),
                text=True,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def _dirty_files(self, cwd: Path | None = None) -> list[str]:
        """Return dirty paths from git status porcelain output."""

        files = []
        for line in collect_workspace_status(cwd or self.active_workspace):
            if len(line) > 2 and line[2] == " ":
                files.append(line[3:].strip())
            elif len(line) > 1 and line[1] == " ":
                files.append(line[2:].strip())
        return files

    def _json_dump(self, data: dict) -> str:
        """Local JSON dump helper to keep imports obvious in this module."""

        import json

        return json.dumps(data, ensure_ascii=False, indent=2)
