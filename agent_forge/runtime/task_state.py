import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class TaskRunStatus(Enum):
    """Lifecycle states for one agent task run."""

    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclass
class TaskCheckpoint:
    """Small resumable snapshot written during AgentLoop execution.

    The checkpoint intentionally stores control-plane facts, not the full chat
    transcript. That keeps state readable and avoids persisting large tool
    outputs or hidden provider internals. Trace remains the full audit stream.
    """

    # Trace run id, also used as filename.
    run_id: str

    # Original task text.
    task: str

    # Workspace where tools are running.
    workspace: str

    # Current lifecycle state.
    status: str

    # Latest completed or in-progress step.
    current_step: int = 0

    # Agent/role currently owning the work.
    agent_name: str = "CodingAgent"

    # Last tool requested by the model.
    last_tool: str = ""

    # Last concise observation/recovery hint.
    last_observation: str = ""

    # Stop reason when blocked/failed/completed.
    stop_reason: str = ""

    # Final user-facing answer, if available.
    final_answer: str = ""

    # How a later run should resume safely.
    resume_hint: str = ""

    # Number of chat messages in memory at checkpoint time.
    messages_count: int = 0

    # Number of tool observations recorded at checkpoint time.
    observations_count: int = 0

    # Updated every save for sorting and audit.
    updated_at: float = field(default_factory=time.time)

    # First creation time.
    created_at: float = field(default_factory=time.time)

    # Small structured metadata such as environment probe and hook decisions.
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return JSON-safe checkpoint data."""

        return asdict(self)


class TaskStateStore:
    """Filesystem-backed task-state store for resume/replay workflows.

    In a production service this would be a database table with transactional
    writes. JSON files are enough for this repository while still proving the
    architecture: state is explicit, inspectable, resumable, and separate from
    trace artifacts.
    """

    def __init__(self, root: str | Path = ".agent_forge/task_state"):
        """Create the state directory without deleting previous runs."""

        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, run_id: str) -> Path:
        """Return the checkpoint path for one run id."""

        return self.root / f"{run_id}.json"

    def start(self, run_id: str, task: str, workspace: str, agent_name: str, metadata: dict | None = None) -> TaskCheckpoint:
        """Create the initial checkpoint."""

        checkpoint = TaskCheckpoint(
            run_id=run_id,
            task=task,
            workspace=str(Path(workspace).resolve()),
            agent_name=agent_name,
            status=TaskRunStatus.CREATED.value,
            resume_hint="Run with --resume-state this_id to seed a continuation from this checkpoint.",
            metadata=metadata or {},
        )
        self.save(checkpoint)
        return checkpoint

    def update(self, checkpoint: TaskCheckpoint, **changes) -> TaskCheckpoint:
        """Update selected fields and persist the checkpoint."""

        for key, value in changes.items():
            if hasattr(checkpoint, key):
                setattr(checkpoint, key, value)
        checkpoint.updated_at = time.time()
        self.save(checkpoint)
        return checkpoint

    def save(self, checkpoint: TaskCheckpoint) -> None:
        """Persist one checkpoint as readable JSON."""

        self.path_for(checkpoint.run_id).write_text(
            json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, run_id: str) -> TaskCheckpoint:
        """Load a checkpoint by id."""

        data = json.loads(self.path_for(run_id).read_text(encoding="utf-8"))
        return TaskCheckpoint(**data)

    def list(self) -> list[TaskCheckpoint]:
        """Return checkpoints newest first."""

        checkpoints = []
        for path in self.root.glob("*.json"):
            try:
                checkpoints.append(TaskCheckpoint(**json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        return sorted(checkpoints, key=lambda item: item.updated_at, reverse=True)

    def resume_summary(self, run_id: str, max_chars: int = 1400) -> str:
        """Build a compact context seed for a continuation run."""

        checkpoint = self.load(run_id)
        summary = (
            f"resume_from_run={checkpoint.run_id}\n"
            f"previous_status={checkpoint.status}\n"
            f"previous_task={checkpoint.task}\n"
            f"last_tool={checkpoint.last_tool}\n"
            f"last_observation={checkpoint.last_observation}\n"
            f"stop_reason={checkpoint.stop_reason}\n"
            f"resume_hint={checkpoint.resume_hint}\n"
            f"final_answer={checkpoint.final_answer}"
        )
        if len(summary) <= max_chars:
            return summary
        return summary[: max_chars - 14] + " [compressed]"


def replay_trace(path: str | Path) -> str:
    """Render a compact human-readable timeline from a trace JSON file."""

    trace_path = Path(path)
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    lines = [
        f"run_id: {trace.get('run_id', '')}",
        f"task: {trace.get('task', '')}",
        f"stop_reason: {trace.get('stop_reason', '')}",
        "",
        "|step|agent|event|success|summary|",
        "|---:|---|---|---|---|",
    ]
    for event in trace.get("events", []):
        summary = (
            event.get("tool_call")
            or event.get("failure_kind")
            or event.get("permission_decision")
            or event.get("error")
            or ""
        )
        if not summary and event.get("event_type") == "context_assembly":
            context = event.get("context") or {}
            summary = f"files={len(context.get('selected_files') or [])} tools={len(context.get('available_tools') or [])}"
        lines.append(
            f"|{event.get('step', 0)}|{event.get('agent_name', '')}|"
            f"{event.get('event_type', '')}|{event.get('success', True)}|{str(summary)[:120]}|"
        )
    return "\n".join(lines) + "\n"
