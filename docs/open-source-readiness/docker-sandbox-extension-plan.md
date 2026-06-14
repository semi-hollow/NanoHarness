# Docker Sandbox Extension Plan

Agent Forge currently supports local execution and git worktree isolation. That
is enough for local development and deterministic verification, but a hosted or
team-facing CodingAgent should run tools in a stronger sandbox.

This document defines the extension point for a Docker-backed sandbox without
mixing container orchestration into the current runtime core.

## Current Boundary

Code:

- `agent_forge/runtime/execution_environment.py`
- `agent_forge/safety/sandbox.py`
- `agent_forge/safety/command_policy.py`
- `agent_forge/runtime/hooks.py`

Current modes:

| mode | behavior |
|---|---|
| `local` | Execute in the current checkout with path and command policy checks. |
| `worktree` | Create an isolated git worktree from HEAD, then execute tools there. |

## Target Docker Mode

Proposed CLI:

```bash
python run_demo.py --mode single --execution-env docker
```

Proposed config:

```python
ExecutionEnvironmentConfig(
    mode="docker",
    workspace=".",
    network_policy="deny",
    image="python:3.11-slim",
    cpu_limit="2",
    memory_limit="2g",
    timeout_seconds=120,
)
```

## Adapter Responsibilities

| responsibility | docker adapter behavior |
|---|---|
| Workspace setup | Copy or mount repository into a disposable container workspace. |
| Dependency setup | Install package in editable mode or reuse a prepared image. |
| Command execution | Run approved commands inside the container, not on the host. |
| Network policy | Start container with network disabled unless explicitly allowed. |
| Secret policy | Do not mount host secret files or provider key env vars by default. |
| Artifact export | Copy trace, usage report, and patch diff out after run. |
| Cleanup | Remove container and temporary volumes unless debug mode is enabled. |

## Interface Sketch

The existing `ExecutionEnvironment` can remain the public facade. A future
implementation can delegate by mode:

```text
ExecutionEnvironment
  LocalExecutionBackend
  WorktreeExecutionBackend
  DockerExecutionBackend
```

Common backend methods:

```text
prepare() -> EnvironmentProbe
validate_command(command: str) -> tuple[bool, str]
run_command(command: str, timeout: float) -> CommandResult
write_manifest(run_dir: Path) -> Path
cleanup() -> None
```

The important design rule: `AgentLoop` and `ToolRegistry` should not know
whether execution happens locally, in a worktree, or in Docker.

## Risks And Controls

| risk | control |
|---|---|
| Container escape | Use rootless Docker or a stronger sandbox for untrusted workloads. |
| Secret leakage | Mount only the workspace and explicit allowlisted env vars. |
| Non-deterministic setup | Pin image digest and package install commands. |
| Slow startup | Reuse prepared image for common Python dependencies. |
| Artifact loss | Always export trace/report/diff before cleanup. |

## Why This Is Not Implemented Yet

The current repository is a runtime-core reference. Docker support is the next
deployment boundary, not a prerequisite for reading the core agent design. The
code already has the correct seam: `ExecutionEnvironment` owns active workspace,
network policy, command validation, manifest, and cleanup.

