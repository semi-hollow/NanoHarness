import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


NETWORK_COMMANDS = {"curl", "wget", "ssh", "scp", "nc", "telnet"}
PROTECTED_GIT_COMMANDS = {"push", "reset", "checkout", "switch", "merge", "rebase"}
PROTECTED_PATH_PARTS = {".git", ".venv", ".agent_forge"}


@dataclass(frozen=True)
class ExecutionEnvironmentConfig:
    """User-selected execution boundary for one run.

    A production coding agent normally runs in an isolated workspace/container
    rather than directly mutating the developer's checkout. This config keeps
    the same control plane visible in a local project: choose local or git
    worktree execution, decide whether network commands are allowed, and decide
    whether the created worktree should remain for inspection.
    """

    # ``local`` runs against the current checkout. ``worktree`` creates an
    # isolated git worktree under ``.agent_forge/worktrees`` and runs tools there.
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


@dataclass(frozen=True)
class EnvironmentProbe:
    """Serializable snapshot of the execution environment.

    This is deliberately verbose because execution isolation is one of the
    first production questions reviewers ask about CodingAgents.
    """

    # Active mode after prepare: local or worktree.
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

    def to_dict(self) -> dict:
        """Return JSON-safe metadata for trace and task-state checkpoints."""

        return asdict(self)


class ExecutionEnvironment:
    """Local execution boundary used by tools and hooks.

    The environment is intentionally not a fake security promise. Local mode is
    path and command policy only. Worktree mode adds a real git-level isolation
    boundary by running the agent in a separate checkout. A production service
    could replace this class with a container or remote VM without changing
    AgentLoop's control flow.
    """

    def __init__(self, config: ExecutionEnvironmentConfig):
        """Store config and resolve the requested workspace early."""

        self.config = config
        self.requested_workspace = Path(config.workspace).resolve()
        self.active_workspace = self.requested_workspace
        self.created_worktree: Path | None = None
        self._notes: list[str] = []
        self._requested_dirty_files: list[str] | None = None

    def prepare(self) -> EnvironmentProbe:
        """Prepare the active workspace and return an auditable probe.

        Worktree mode creates an isolated checkout at HEAD. It intentionally
        does not copy uncommitted local edits; that keeps runs reproducible and
        makes dirty working-tree state visible in the probe.
        """

        if self.config.mode not in {"local", "worktree"}:
            raise ValueError(f"unsupported execution environment: {self.config.mode}")

        self._requested_dirty_files = self._dirty_files(self.requested_workspace)
        if self.config.mode == "worktree":
            self._prepare_worktree()

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
        """Remove a temporary worktree only when configured to do so."""

        if not self.created_worktree or self.config.keep_worktree:
            return
        if self.created_worktree.exists():
            shutil.rmtree(self.created_worktree, ignore_errors=True)
        self._git_output(["git", "worktree", "prune"], cwd=self.requested_workspace)

    def write_manifest(self, output_dir: str | Path) -> Path:
        """Write environment metadata for session reports and run audits."""

        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        path = output / "execution_environment.json"
        path.write_text(
            self._json_dump(
                {
                    "probe": self.probe().to_dict(),
                    "worktree_created": str(self.created_worktree or ""),
                    "cleanup_policy": "keep" if self.config.keep_worktree else "remove",
                }
            ),
            encoding="utf-8",
        )
        return path

    def diff(self) -> str:
        """Return git diff for the active workspace."""

        return self._git_output(["git", "diff", "--", "."], cwd=self.active_workspace)

    def describe(self) -> str:
        """Human-readable environment summary for prompts and reports."""

        probe = self.probe()
        return (
            f"execution_environment mode={probe.mode}; "
            f"active_workspace={probe.active_workspace}; "
            f"network_policy={probe.network_policy}; "
            f"branch={probe.current_branch}; dirty={probe.dirty}"
        )

    def _prepare_worktree(self) -> None:
        """Create a detached git worktree for side-effect isolation."""

        if not (self.requested_workspace / ".git").exists():
            git_root = self._git_output(["git", "rev-parse", "--show-toplevel"])
            if not git_root:
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

        output = self._git_output(["git", "status", "--porcelain"], cwd=cwd or self.active_workspace)
        files = []
        for line in output.splitlines():
            if len(line) > 2 and line[2] == " ":
                files.append(line[3:].strip())
            elif len(line) > 1 and line[1] == " ":
                files.append(line[2:].strip())
        return files

    def _json_dump(self, data: dict) -> str:
        """Local JSON dump helper to keep imports obvious in this module."""

        import json

        return json.dumps(data, ensure_ascii=False, indent=2)
