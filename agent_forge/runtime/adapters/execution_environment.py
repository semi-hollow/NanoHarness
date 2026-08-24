"""为 Agent 工具提供可探测、可限制、可留证的代码执行 Adapter。

系统角色：在 Sandbox/CommandPolicy 已作逻辑判断后，决定命令最终在哪个 workspace、
临时 worktree 或容器运行，并把环境边界投影为可审计 manifest。
输入：ExecutionEnvironmentConfig、路径和命令；输出：受限执行结果、Diff 与环境证据。
相邻边界：Policy 决定“是否允许”；本 Adapter 负责“在哪里、以什么资源边界执行”。

折叠导航：1 config/probe；2 guards；3 evidence；4 execute；5 backends。
"""

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
from typing import Callable

from agent_forge.runtime.adapters.git_workspace import (
    collect_workspace_diff,
    collect_workspace_status,
)
from agent_forge.runtime.ports.environment import EnvironmentPort

NETWORK_COMMANDS = {"curl", "wget", "ssh", "scp", "nc", "telnet"}
PROTECTED_GIT_COMMANDS = {"push", "reset", "checkout", "switch", "merge", "rebase"}
PROTECTED_PATH_PARTS = {".git", ".venv", ".agent_forge"}


# region 1. 配置与探测 contract：冻结执行模式、资源限制和实际环境事实
@dataclass(frozen=True, kw_only=True)
class ExecutionEnvironmentConfig:
    """执行环境声明；只描述边界，不在构造时启动容器或创建 worktree。"""

    mode: str = "local"
    workspace: str = "."
    run_id: str = ""
    worktree_root: str = ".agent_forge/internal/cache/worktrees"
    network_policy: str = "deny"
    keep_worktree: bool = True
    container_runtime: str = "docker"
    container_image: str = "python:3.11-slim"
    container_cpus: float = 1.0
    container_memory: str = "1g"
    container_pids_limit: int = 256
    container_read_only: bool = True
    snapshot_root: str = ".agent_forge/internal/cache/snapshots"
    # 只由 checkpoint resume 注入；fresh run 不能自行选择已有执行树。
    reattach_workspace: str = ""


@dataclass(frozen=True, kw_only=True)
class EnvironmentProbe:
    """环境准备后的只读事实快照，供 trace、manifest 和报告消费。"""

    mode: str
    requested_workspace: str
    active_workspace: str
    git_root: str
    current_branch: str
    head_sha: str
    origin_url: str
    dirty: bool
    dirty_files: list[str]
    network_policy: str
    python_executable: str
    notes: list[str] = field(default_factory=list)
    container_runtime: str = ""
    container_image: str = ""
    container_image_id: str = ""
    container_id: str = ""
    resource_limits: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
# endregion 1. 配置与探测 contract 结束


class ExecutionEnvironment(EnvironmentPort):
    """准备 local/worktree/OCI 环境，并作为命令执行的唯一基础设施入口。

    ``prepare`` 固定运行边界，``execute_command`` 在该边界内执行已经由上层 Tool
    与 Hook 授权的 argv；其余方法负责检查和留证。
    """

    def __init__(
        self,
        config: ExecutionEnvironmentConfig,
        *,
        oci_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        executable_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
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

    # region 1. 生命周期与模式选择：prepare 一次，probe 多次读取真实边界
    # 主要入口：创建并探测 local、detached worktree 或 OCI 执行环境。
    def prepare(self) -> EnvironmentProbe:
        """按 local、worktree 或 container 模式准备受约束执行环境。"""

        if self.config.mode not in {"local", "worktree", "container"}:
            raise ValueError(f"unsupported execution environment: {self.config.mode}")
        self._validate_config()

        self._requested_dirty_files = self._dirty_files(self.requested_workspace)
        if self.config.mode == "worktree":
            if self.config.reattach_workspace:
                self._reattach_worktree()
            else:
                self._prepare_worktree()
        elif self.config.mode == "container":
            if self.config.reattach_workspace:
                raise RuntimeError(
                    "container execution resume cannot reattach a durable execution workspace"
                )
            self._prepare_container()
        elif self.config.reattach_workspace:
            requested = self.requested_workspace.resolve()
            resumed = Path(self.config.reattach_workspace).expanduser().resolve()
            if resumed != requested:
                raise RuntimeError("local execution resume must use requested workspace")

        return self.probe()

    def probe(self) -> EnvironmentProbe:
        """读取当前环境事实；不会创建环境，也不会执行用户任务。"""

        git_root = self._git_output(
            ["git", "rev-parse", "--show-toplevel"], cwd=self.active_workspace
        )
        branch = (
            self._git_output(
                ["git", "branch", "--show-current"], cwd=self.active_workspace
            )
            or "detached"
        )
        head_sha = self._git_output(
            ["git", "rev-parse", "HEAD"], cwd=self.active_workspace
        )
        origin_url = self.redact(
            self._git_output(
                ["git", "remote", "get-url", "origin"], cwd=self.active_workspace
            )
        )
        dirty_files = (
            list(self._requested_dirty_files)
            if self._requested_dirty_files is not None
            else self._dirty_files(self.requested_workspace)
        )
        dirty = bool(dirty_files)
        return EnvironmentProbe(
            mode=self._effective_mode(),
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
            container_image=self.config.container_image
            if self.config.mode == "container"
            else "",
            container_image_id=self._container_image_id,
            container_id=self._container_id,
            resource_limits=self._resource_limits()
            if self.config.mode == "container"
            else {},
        )

    def _effective_mode(self) -> str:
        """报告真实执行边界，避免 worktree fallback 被持久化成不可恢复身份。"""

        if (
            self.config.mode == "worktree"
            and self.active_workspace == self.requested_workspace
            and self.created_worktree is None
        ):
            return "local"
        return self.config.mode
    # endregion 1. 生命周期与模式选择结束

    # region 2. 路径、命令和敏感信息：执行前统一 fail closed / redact
    def resolve_path(self, path: str | Path) -> Path:
        """把输入路径解析为当前 active workspace 下的绝对路径。"""

        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.active_workspace / candidate
        return candidate.resolve()

    def validate_path(self, path: str | Path) -> tuple[bool, str]:
        """检查路径是否逃逸 workspace 或命中受保护目录。"""

        resolved = self.resolve_path(path)
        try:
            relative = resolved.relative_to(self.active_workspace)
        except ValueError:
            return False, "path escapes execution environment"
        if any(part in PROTECTED_PATH_PARTS for part in relative.parts):
            return (
                False,
                f"protected path blocked by execution environment: {resolved.name}",
            )
        return True, "path allowed by execution environment"

    def validate_command(self, command: str) -> tuple[bool, str]:
        """执行环境层的最后一道命令边界检查，不替代 CommandPolicy。"""

        try:
            parts = shlex.split(command)
        except ValueError as exc:
            return False, f"invalid command: {exc}"
        if not parts:
            return False, "empty command"

        executable = parts[0].lower()
        if self.config.network_policy == "deny" and executable in NETWORK_COMMANDS:
            return (
                False,
                f"network command blocked by execution environment: {executable}",
            )

        if executable == "git" and len(parts) > 1:
            subcommand = parts[1].lower()
            if subcommand in PROTECTED_GIT_COMMANDS:
                return False, f"git {subcommand} blocked by execution environment"

        return True, "command allowed by execution environment"

    def redact(self, text: str) -> str:
        """清除 Observation 或 manifest 中可识别的凭据。"""

        if not text:
            return text
        patterns = [
            (
                r"Authorization:\s*Bearer\s+[A-Za-z0-9._\-]+",
                "Authorization: Bearer [redacted]",
            ),
            (
                r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*[A-Za-z0-9._\-]{12,}",
                r"\1=[redacted]",
            ),
            (r"sk-[A-Za-z0-9_\-]{16,}", "sk-[redacted]"),
            (r"Bearer\s+[A-Za-z0-9._\-]{16,}", "Bearer [redacted]"),
        ]
        redacted = text
        for pattern, replacement in patterns:
            redacted = re.sub(pattern, replacement, redacted)
        for secret_environment_name in (
            "DEEPSEEK_API_KEY",
            "AGENT_FORGE_API_KEY",
            "OPENAI_API_KEY",
        ):
            secret_environment_value = os.getenv(secret_environment_name, "")
            if secret_environment_value and len(secret_environment_value) >= 8:
                redacted = redacted.replace(
                    secret_environment_value,
                    f"[redacted:{secret_environment_name}]",
                )
        return redacted

    # endregion 2. 路径、命令和敏感信息结束

    # region 3. Cleanup 与 evidence：只清理本次资源，并发布 manifest/Diff
    def cleanup(self) -> None:
        """释放本次运行创建的容器和临时快照。"""

        container_target = self._container_id or self._container_name
        if container_target and self._container_runtime_path:
            container_removal_process = self._oci_runner(
                [self._container_runtime_path, "rm", "-f", container_target],
                text=True,
                capture_output=True,
                timeout=30,
            )
            if container_removal_process.returncode == 0:
                self._notes.append("removed OCI container")
            else:
                cleanup_failure_detail = self.redact(
                    (
                        container_removal_process.stderr
                        or container_removal_process.stdout
                        or "unknown error"
                    ).strip()
                )
                self._notes.append(
                    f"OCI container cleanup failed: {cleanup_failure_detail}"
                )
            self._container_id = ""

        self._cleanup_snapshot()

    def write_manifest(self, output_dir: str | Path) -> Path:
        """把环境事实和命令历史写成可审计 manifest。"""

        manifest_directory = Path(output_dir)
        manifest_directory.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_directory / "execution_environment.json"
        environment_manifest = {
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
            environment_manifest["container"] = {
                "runtime": self.config.container_runtime,
                "runtime_path": self._container_runtime_path,
                "image": self.config.container_image,
                "image_id": self._container_image_id,
                "container_id": self._container_id,
                "network_policy": self.config.network_policy,
                "root_read_only": self.config.container_read_only,
                "resource_limits": self._resource_limits(),
                "start_command": self._container_start_command,
                "recreate_command": self._container_start_command
                if replayable_after_cleanup
                else [],
                "replayable_after_cleanup": replayable_after_cleanup,
                "exec_prefix": [
                    self._container_runtime_path,
                    "exec",
                    self._container_id,
                ],
                "command_history": self._command_history,
                "boundary_note": (
                    "Commands run in the OCI container; host file tools remain constrained to the mounted snapshot."
                ),
            }
        manifest_path.write_text(
            self._json_dump(environment_manifest),
            encoding="utf-8",
        )
        return manifest_path

    def diff(self) -> str:
        """读取 active workspace 相对基线的 candidate diff。"""

        return collect_workspace_diff(self.active_workspace)

    def render_boundary_summary(self) -> str:
        """把真实执行边界渲染为模型可见的一行约束摘要。"""

        probe = self.probe()
        return (
            f"execution_environment mode={probe.mode}; "
            f"active_workspace={probe.active_workspace}; "
            f"network_policy={probe.network_policy}; "
            f"branch={probe.current_branch}; dirty={probe.dirty}"
        )

    # endregion 3. Cleanup 与 evidence 结束

    # region 4. 命令执行：根据 effective mode 选择唯一 backend
    def execute_command(
        self, argv: list[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        """执行上层已经授权的 argv，并记录耗时与输出规模。

        本方法只接收参数数组，local 模式固定 ``shell=False``；因此不会解释管道、重定向
        或命令替换。它类似 Java 中封装后的 ProcessBuilder，而不是开放一个 shell 会话。
        """

        if not argv:
            raise ValueError("empty command argv")
        started = time.monotonic()
        if self.config.mode == "container":
            if not self._container_id or not self._container_runtime_path:
                raise RuntimeError("container execution environment is not prepared")
            runtime_command = [
                self._container_runtime_path,
                "exec",
                self._container_id,
                *argv,
            ]
            command_process = self._oci_runner(
                runtime_command,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        else:
            normalized_command = (
                [sys.executable, *argv[1:]]
                if argv[0] in {"python", "python3", "python3.11"}
                else list(argv)
            )
            runtime_command = normalized_command
            command_process = subprocess.run(
                normalized_command,
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
                "returncode": command_process.returncode,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "stdout_chars": len(command_process.stdout or ""),
                "stderr_chars": len(command_process.stderr or ""),
            }
        )
        return command_process
    # endregion 4. 命令执行结束

    # region 5. Backend 准备：container / snapshot / worktree 与资源限制
    def _prepare_container(self) -> None:
        runtime = self._executable_resolver(self.config.container_runtime)
        if not runtime:
            raise RuntimeError(
                f"container runtime not found: {self.config.container_runtime}; install it or use local/worktree mode"
            )
        self._container_runtime_path = str(runtime)
        inspect = self._oci_runner(
            [
                self._container_runtime_path,
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                self.config.container_image,
            ],
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

        safe_run_id = re.sub(
            r"[^a-zA-Z0-9_.-]+", "-", self.config.run_id or uuid.uuid4().hex[:8]
        )
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
        getuid: Callable[[], int] | None = getattr(os, "getuid", None)
        getgid: Callable[[], int] | None = getattr(os, "getgid", None)
        if getuid is not None and getgid is not None:
            command.extend(["--user", f"{getuid()}:{getgid()}"])
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
        self._notes.append(
            "started constrained OCI container over isolated workspace snapshot"
        )

    def _prepare_snapshot(self) -> None:
        git_root = self._git_output(
            ["git", "rev-parse", "--show-toplevel"], cwd=self.requested_workspace
        )
        if git_root:
            self._prepare_worktree(required=True)
            return
        run_id = self.config.run_id or uuid.uuid4().hex[:8]
        snapshot_path = (
            self.requested_workspace / self.config.snapshot_root / run_id
        ).resolve()
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        if snapshot_path.exists():
            snapshot_path = snapshot_path.with_name(
                f"{snapshot_path.name}-{uuid.uuid4().hex[:6]}"
            )
        shutil.copytree(
            self.requested_workspace,
            snapshot_path,
            ignore=shutil.ignore_patterns(
                ".git", ".agent_forge", ".venv", "__pycache__", "*.pyc"
            ),
        )
        self.created_snapshot = snapshot_path
        self.active_workspace = snapshot_path
        self._notes.append("created isolated filesystem snapshot for non-git workspace")

    def _resource_limits(self) -> dict[str, object]:
        return {
            "cpus": self.config.container_cpus,
            "memory": self.config.container_memory,
            "pids": self.config.container_pids_limit,
        }

    def _validate_config(self) -> None:
        if self.config.network_policy not in {"deny", "allow"}:
            raise ValueError(
                f"unsupported network policy: {self.config.network_policy}"
            )
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
        snapshot = self.created_worktree or self.created_snapshot
        if not snapshot or self.config.keep_worktree:
            return
        if snapshot.exists():
            shutil.rmtree(snapshot, ignore_errors=True)
        self._git_output(["git", "worktree", "prune"], cwd=self.requested_workspace)

    def _prepare_worktree(self, required: bool = False) -> None:
        if not (self.requested_workspace / ".git").exists():
            git_root = self._git_output(["git", "rev-parse", "--show-toplevel"])
            if not git_root:
                if required:
                    raise RuntimeError(
                        "isolated git snapshot requested but no git repository was found"
                    )
                self._notes.append(
                    "worktree requested but no git repository was found; using local mode"
                )
                self.active_workspace = self.requested_workspace
                return

        run_id = self.config.run_id or uuid.uuid4().hex[:8]
        worktree_path = (
            self.requested_workspace / self.config.worktree_root / run_id
        ).resolve()
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        if worktree_path.exists():
            collision_suffix = uuid.uuid4().hex[:6]
            worktree_path = worktree_path.with_name(
                f"{worktree_path.name}-{collision_suffix}"
            )

        worktree_creation_process = subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree_path), "HEAD"],
            cwd=str(self.requested_workspace),
            text=True,
            capture_output=True,
        )
        if worktree_creation_process.returncode != 0:
            failure_detail = (
                worktree_creation_process.stderr or worktree_creation_process.stdout
            ).strip()
            if required:
                raise RuntimeError(
                    f"isolated worktree creation failed: {failure_detail}"
                )
            self._notes.append(f"worktree creation failed: {failure_detail}")
            self.active_workspace = self.requested_workspace
            return

        self.created_worktree = worktree_path
        self.active_workspace = worktree_path
        self._notes.append("created isolated git worktree from HEAD")

    def _reattach_worktree(self) -> None:
        """把 unfinished Turn 重新绑定到 checkpoint 指向的原 worktree。"""

        configured_root = Path(self.config.worktree_root)
        worktree_root = (
            configured_root
            if configured_root.is_absolute()
            else self.requested_workspace / configured_root
        ).resolve()
        raw_candidate = Path(self.config.reattach_workspace).expanduser()
        candidate = (
            raw_candidate
            if raw_candidate.is_absolute()
            else self.requested_workspace / raw_candidate
        ).resolve()
        try:
            candidate.relative_to(worktree_root)
        except ValueError as exc:
            raise RuntimeError(
                "resume execution workspace escapes configured worktree root"
            ) from exc
        if not candidate.is_dir():
            raise RuntimeError(
                f"resume execution workspace no longer exists: {candidate}"
            )
        if self._git_output(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=candidate
        ) != "true":
            raise RuntimeError("resume execution workspace is not a Git worktree")
        self.created_worktree = candidate
        self.active_workspace = candidate
        self._notes.append("reattached unfinished Turn execution worktree")
    # endregion 5. Backend 准备结束

    # region 6. Git / serialization helper：不拥有策略，只读取 backend 事实
    def _git_output(self, command: list[str], cwd: Path | None = None) -> str:
        try:
            git_process = subprocess.run(
                command,
                cwd=str(cwd or self.requested_workspace),
                text=True,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if git_process.returncode != 0:
            return ""
        return git_process.stdout.strip()

    def _dirty_files(self, cwd: Path | None = None) -> list[str]:
        files = []
        for line in collect_workspace_status(cwd or self.active_workspace):
            if len(line) > 2 and line[2] == " ":
                files.append(line[3:].strip())
            elif len(line) > 1 and line[1] == " ":
                files.append(line[2:].strip())
        return files

    def _json_dump(self, data: dict) -> str:
        import json

        return json.dumps(data, ensure_ascii=False, indent=2)
    # endregion 6. Git / serialization helper 结束
