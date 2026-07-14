from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class FileEvidenceCatalog:
    """Discover immutable run artifacts for Workbench read models."""

    def __init__(self, project_dir: Path) -> None:
        # Keep the caller-visible path spelling (notably /var vs /private/var
        # on macOS) while still making relative inputs absolute.
        self.project_dir = project_dir.absolute()

    def latest_run_dir(self) -> Path | None:
        latest = self.project_dir / ".agent_forge/latest"
        runs_dir = self.project_dir / ".agent_forge/runs"
        candidates: list[Path] = []
        bench_run = self._run_dir_from_pointer(latest / "bench.txt")
        if bench_run and _is_under(bench_run, runs_dir):
            candidates.append(bench_run)
        latest_run = self._run_dir_from_pointer(latest / "run.txt")
        if latest_run and _is_under(latest_run, runs_dir):
            candidates.append(latest_run)
        if runs_dir.exists():
            candidates.extend(path for path in runs_dir.iterdir() if path.is_dir())
        if candidates:
            unique = {path.resolve(): path for path in candidates}
            return max(unique.values(), key=lambda path: path.stat().st_mtime)
        return latest_run

    def latest_report_path(self) -> str:
        run_dir = self.latest_run_dir()
        if run_dir:
            for name in (
                "report.md",
                "fanout/fanout_report.md",
                "multi_agent/multi_agent_report.md",
                "usage_report.md",
            ):
                candidate = run_dir / name
                if candidate.exists():
                    return str(candidate)
        return ""

    def read_latest_report(self) -> str:
        path = self.latest_report_path()
        if not path:
            return "No report yet. Run DeepSeek Agent Run or SWE-bench Sample first."
        return Path(path).read_text(encoding="utf-8")

    def latest_trace_path(self) -> Path | None:
        run_dir = self.latest_run_dir()
        if not run_dir:
            return None
        direct = run_dir / "trace.json"
        if direct.exists():
            return direct
        traces = sorted(run_dir.glob("cases/**/trace.json"))
        return max(traces, key=lambda path: path.stat().st_mtime) if traces else None

    def latest_usage_path(self) -> Path | None:
        run_dir = self.latest_run_dir()
        if not run_dir:
            return None
        direct = run_dir / "usage.json"
        if direct.exists():
            return direct
        usages = sorted(run_dir.glob("cases/**/usage.json"))
        return max(usages, key=lambda path: path.stat().st_mtime) if usages else None

    def latest_comparison_path(self) -> Path | None:
        run_dir = self.latest_run_dir()
        if not run_dir:
            return None
        candidates = [run_dir / "comparison.json"]
        candidates.extend(sorted(run_dir.glob("cases/*/comparison.json")))
        candidates.extend(sorted(run_dir.glob("cases/*/*/comparison.json")))
        return _newest_existing(candidates)

    def latest_multi_agent_summary_path(self) -> Path | None:
        run_dir = self.latest_run_dir()
        if not run_dir:
            return None
        candidates = [run_dir / "multi_agent/multi_agent_summary.json"]
        candidates.extend(
            sorted(run_dir.glob("cases/**/multi_agent/multi_agent_summary.json"))
        )
        return _newest_existing(candidates)

    def latest_fanout_summary_path(self) -> Path | None:
        run_dir = self.latest_run_dir()
        if not run_dir:
            return None
        candidate = run_dir / "fanout" / "fanout_summary.json"
        return candidate if candidate.exists() else None

    def trace_paths(self) -> list[tuple[str, Path]]:
        """Return multi-agent evidence first, then the single-agent baseline."""

        run_dir = self.latest_run_dir()
        if run_dir is None:
            return []
        direct = run_dir / "trace.json"
        if direct.exists():
            return [("AgentLoop", direct)]
        traces = list(run_dir.glob("cases/**/trace.json"))

        def trace_order(path: Path) -> tuple[int, float]:
            parts = set(path.parts)
            priority = 0 if "multi" in parts else 1 if "single" in parts else 2
            return priority, -path.stat().st_mtime

        labelled: list[tuple[str, Path]] = []
        seen_labels: set[str] = set()
        for path in sorted(traces, key=trace_order):
            if "multi" in path.parts:
                label = "Multi-Agent Runtime"
            elif "single" in path.parts:
                label = "Single-Agent Runtime"
            else:
                label = trace_scope_label(path)
            if label in seen_labels:
                continue
            seen_labels.add(label)
            labelled.append((label, path))
        return labelled

    def latest_feedback_path(self) -> Path | None:
        trace_path = self.latest_trace_path()
        run_dir = self.latest_run_dir()
        candidates: list[Path] = []
        if trace_path is not None:
            candidates.append(trace_path.parent / "feedback.json")
        if run_dir is not None:
            candidates.append(run_dir / "feedback.json")
            candidates.extend(run_dir.glob("cases/**/feedback.json"))
        return _newest_existing(candidates)

    def latest_feedback_outcome(self) -> str:
        feedback = read_json_file(self.latest_feedback_path())
        return str(feedback.get("outcome") or "unreviewed")

    def latest_result_record(self) -> dict[str, Any]:
        run_dir = self.latest_run_dir()
        if run_dir is None:
            return {}
        results = read_json_file(run_dir / "results.json")
        case_results = results.get("case_results") or []
        return (
            case_results[0]
            if case_results and isinstance(case_results[0], dict)
            else {}
        )

    def latest_direct_baseline_record(self) -> dict[str, Any]:
        run_dir = self.latest_run_dir()
        path = (
            run_dir / "direct_baseline_predictions.jsonl"
            if run_dir is not None
            else None
        )
        if path is None or not path.exists():
            return {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                return record
        return {}

    def _run_dir_from_pointer(self, pointer: Path) -> Path | None:
        if not pointer.exists():
            return None
        run_dir = Path(pointer.read_text(encoding="utf-8").strip())
        if not run_dir.is_absolute():
            run_dir = self.project_dir / run_dir
        return run_dir if run_dir.exists() else None


def read_json_file(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": str(exc), "path": str(path)}
    return data if isinstance(data, dict) else {}


def trace_scope_label(trace_path: Path | None) -> str:
    if not trace_path:
        return "unknown trace"
    parts = set(trace_path.parts)
    text = str(trace_path)
    if "verify" in parts:
        return "verify smoke trace"
    if "multi" in parts or "__multi" in text:
        return "multi-agent trace"
    if "single" in parts or "__single" in text:
        return "single-agent trace"
    return "agent run trace"


def _newest_existing(candidates: list[Path]) -> Path | None:
    existing = [path for path in candidates if path.exists()]
    return max(existing, key=lambda path: path.stat().st_mtime) if existing else None


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
