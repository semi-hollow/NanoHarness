import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class RunSession:
    """Persistent session metadata for one agent run."""

    # Unique folder/run key.
    session_id: str

    # Workspace the run operated on; useful before rollback/resume.
    workspace: str

    # single/multi/workflow.
    mode: str

    # Original user task.
    task: str

    # Epoch timestamp for sorting/audit.
    created_at: float

    # running/completed; could become failed/cancelled in a larger system.
    status: str = "running"

    # JSON trace path.
    trace_file: str = ""

    # Final human answer.
    final_answer: str = ""

    # Pointers to report/metrics/diff/trace files.
    artifacts: dict[str, str] = field(default_factory=dict)


class SessionStore:
    """Filesystem-backed run/session store.

    OpenCode-like tools are not one-off scripts: they need resumable, auditable
    runs. This store keeps the implementation simple with JSON/JSONL artifacts
    while modeling the same production concept as a database-backed session
    table.
    """

    def __init__(self, root: str | Path = ".agent_forge/runs"):
        """Create the session root without deleting any previous runs."""

        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def start(self, workspace: str, mode: str, task: str) -> tuple[RunSession, Path]:
        """Create one new run directory and session.json."""

        # Timestamp + short random suffix gives readable but collision-resistant
        # local run ids.
        session_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        run_dir = self.root / session_id
        run_dir.mkdir(parents=True, exist_ok=False)
        session = RunSession(
            session_id=session_id,
            workspace=str(Path(workspace).resolve()),
            mode=mode,
            task=task,
            created_at=time.time(),
        )
        self.write(session, run_dir)
        return session, run_dir

    def list_sessions(self) -> list[Path]:
        """Return run directories newest first."""

        return sorted((path for path in self.root.iterdir() if path.is_dir()), reverse=True)

    def report_path(self, session_id: str) -> Path:
        """Return the human report path for a session id."""

        return self.root / session_id / "report.md"

    def load(self, session_id: str) -> RunSession:
        """Load a previous run for resume/context inheritance."""

        path = self.root / session_id / "session.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return RunSession(**data)

    def summary_for_resume(self, session_id: str, max_chars: int = 1200) -> str:
        """Return a compact previous-run summary for the next AgentLoop.

        Resume should seed context, not replay previous messages blindly. The
        ContextStrategy still decides whether this summary is relevant.
        """

        report = self.report_path(session_id)
        if report.exists():
            text = report.read_text(encoding="utf-8")
        else:
            session = self.load(session_id)
            text = f"previous_task={session.task}\nprevious_final_answer={session.final_answer}"
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 14] + " [compressed]"

    def rollback(self, session_id: str, workspace: str | Path) -> list[str]:
        """Restore files from the rollback bundle written before a run.

        This is intentionally explicit and local. It only copies files captured
        under ``rollback/`` back into the workspace; it does not run git reset or
        delete unrelated files.
        """

        run_dir = self.root / session_id
        rollback_dir = run_dir / "rollback"
        workspace_path = Path(workspace).resolve()
        restored = []
        if not rollback_dir.exists():
            return restored
        for source in rollback_dir.rglob("*"):
            if not source.is_file():
                continue
            rel = source.relative_to(rollback_dir)
            destination = workspace_path / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            restored.append(rel.as_posix())
        return restored

    def finish(self, session: RunSession, run_dir: str | Path, final_answer: str, trace_file: str) -> None:
        """Mark a session completed and persist final pointers."""

        session.status = "completed"
        session.final_answer = final_answer
        session.trace_file = trace_file
        session.artifacts.update(
            {
                "trace": trace_file,
                "report": str(Path(run_dir) / "report.md"),
                "metrics": str(Path(run_dir) / "metrics.json"),
                "diff": str(Path(run_dir) / "diff.patch"),
            }
        )
        self.write(session, run_dir)

    def append_event(self, run_dir: str | Path, event: dict) -> None:
        """Append one JSONL event for quick terminal/session inspection."""

        with (Path(run_dir) / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def write(self, session: RunSession, run_dir: str | Path) -> None:
        """Write session metadata in a readable JSON file."""

        Path(run_dir, "session.json").write_text(
            json.dumps(asdict(session), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
