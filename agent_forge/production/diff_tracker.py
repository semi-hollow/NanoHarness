import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FileSnapshot:
    """Hash and content for one tracked source file before a run."""

    # Workspace-relative path.
    path: str

    # Fast equality check for before/after comparison.
    sha256: str

    # Original content for rollback bundle.
    content: str


@dataclass
class DiffSummary:
    """Machine-readable change summary for run reports and rollback."""

    # Workspace-relative files whose hashes changed.
    changed_files: list[str] = field(default_factory=list)

    # git diff when available, simple fallback otherwise.
    patch: str = ""

    # Before/after hashes let reports detect changes even outside git.
    before_hashes: dict[str, str] = field(default_factory=dict)
    after_hashes: dict[str, str] = field(default_factory=dict)


class DiffTracker:
    """Track source changes caused by one agent run.

    A production coding agent must answer "what did you change and how do I
    undo it?" This tracker records file hashes, git diff when available, and a
    rollback-friendly snapshot for normal text files under the workspace.
    """

    def __init__(self, workspace: str | Path):
        """Create a tracker for one workspace root."""

        self.workspace = Path(workspace).resolve()
        self.before: dict[str, FileSnapshot] = {}

    def capture_before(self) -> None:
        """Snapshot current source files before the agent starts editing."""

        self.before = {}
        for path in self._tracked_files():
            rel = path.relative_to(self.workspace).as_posix()
            content = path.read_text(encoding="utf-8")
            self.before[rel] = FileSnapshot(rel, self._hash(content), content)

    def summarize_after(self) -> DiffSummary:
        """Compare current files with the before snapshot."""

        summary = DiffSummary(before_hashes={k: v.sha256 for k, v in self.before.items()})
        after_files = {}
        for path in self._tracked_files():
            rel = path.relative_to(self.workspace).as_posix()
            content = path.read_text(encoding="utf-8")
            after_files[rel] = self._hash(content)
        summary.after_hashes = after_files
        all_paths = sorted(set(summary.before_hashes) | set(summary.after_hashes))
        summary.changed_files = [
            path for path in all_paths if summary.before_hashes.get(path) != summary.after_hashes.get(path)
        ]
        summary.patch = self._git_diff() or self._simple_patch(summary.changed_files)
        return summary

    def write_rollback_bundle(self, output_dir: str | Path, changed_files: list[str] | None = None) -> None:
        """Write before contents for changed files so a run can be reverted.

        `capture_before` snapshots all small source files because it cannot know
        which files the agent will touch. The rollback bundle should be much
        narrower: only files that actually changed need previous contents. This
        keeps `.agent_forge/runs/*/rollback` readable instead of duplicating the
        project on every run.
        """

        output = Path(output_dir)
        changed = set(changed_files or self.summarize_after().changed_files)
        snapshots = {rel: snapshot for rel, snapshot in self.before.items() if rel in changed}
        if not snapshots:
            return
        rollback_dir = output / "rollback"
        rollback_dir.mkdir(parents=True, exist_ok=True)
        for rel, snapshot in snapshots.items():
            destination = rollback_dir / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(snapshot.content, encoding="utf-8")

    def _tracked_files(self) -> list[Path]:
        """Return small text source files and skip generated/local folders.

        This intentionally ignores large/binary/generated files. Rollback should
        be safe and readable for this teaching runtime, not a full backup system.
        """

        skip_parts = {".git", ".venv", ".agent_forge", "__pycache__", ".pytest_cache"}
        suffixes = {".py", ".md", ".toml", ".json", ".sh", ".txt"}
        files = []
        for path in self.workspace.rglob("*"):
            if not path.is_file():
                continue
            if any(part in skip_parts for part in path.parts):
                continue
            if path.suffix not in suffixes:
                continue
            try:
                if path.stat().st_size > 250_000:
                    continue
                path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            files.append(path)
        return files

    def _git_diff(self) -> str:
        """Use git diff when the workspace is inside a git repository."""

        try:
            proc = subprocess.run(
                ["git", "diff", "--", "."],
                cwd=str(self.workspace),
                text=True,
                capture_output=True,
                timeout=10,
            )
            return proc.stdout[:50_000] if proc.returncode == 0 else ""
        except Exception:
            return ""

    def _simple_patch(self, changed_files: list[str]) -> str:
        """Fallback patch summary when git diff is unavailable."""

        return "\n".join(f"changed: {path}" for path in changed_files)

    def _hash(self, content: str) -> str:
        """Return stable sha256 for text content."""

        return hashlib.sha256(content.encode("utf-8")).hexdigest()
