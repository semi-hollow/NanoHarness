from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class UiCommand:
    """A browser-submitted agent action after server-side validation.

    The UI lets users configure real runs in a page, but the server still turns
    those settings into a bounded command shape. Secrets are passed through
    environment variables and never appear in ``display_command``.
    """

    title: str
    command: list[str]
    display_command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class UiJob:
    """One background action started by the local browser workbench.

    ``command`` is the actual subprocess argv. ``display_command`` is the
    redacted, human-readable equivalent shown in the page. Keeping both fields
    separate prevents accidental API key leaks while still making runs auditable.
    """

    id: str
    title: str
    command: list[str]
    display_command: list[str]
    env_overrides: dict[str, str] = field(default_factory=dict, repr=False)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    status: str = "running"
    exit_code: int | None = None
    output: str = ""


class UiState:
    """Shared in-memory state for the local UI server."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.jobs: dict[str, UiJob] = {}
        self.lock = threading.Lock()

    def start_job(self, command: UiCommand) -> UiJob:
        """Start a background subprocess and keep its output available."""

        job = UiJob(
            id=uuid.uuid4().hex[:10],
            title=command.title,
            command=command.command,
            display_command=command.display_command or command.command,
            env_overrides=command.env,
        )
        with self.lock:
            self.jobs[job.id] = job
        thread = threading.Thread(target=self._run_job, args=(job,), daemon=True)
        thread.start()
        return job

    def _run_job(self, job: UiJob) -> None:
        """Run one fixed command and capture combined stdout/stderr."""

        try:
            env = os.environ.copy()
            env.update(job.env_overrides)
            process = subprocess.run(
                job.command,
                cwd=self.project_dir,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            job.exit_code = process.returncode
            job.output = process.stdout
            job.status = "succeeded" if process.returncode == 0 else "failed"
        except Exception as exc:
            job.exit_code = -1
            job.output = str(exc)
            job.status = "failed"
        finally:
            job.finished_at = time.time()

    def get_job(self, job_id: str) -> UiJob | None:
        """Return one job by id."""

        with self.lock:
            return self.jobs.get(job_id)

    def latest_jobs(self) -> list[UiJob]:
        """Return recent jobs, newest first."""

        with self.lock:
            return sorted(self.jobs.values(), key=lambda job: job.started_at, reverse=True)[:20]


class ForgeUiHandler(BaseHTTPRequestHandler):
    """HTTP handler for the local browser UI."""

    state: UiState

    def do_GET(self) -> None:
        """Serve the page and read-only JSON endpoints."""

        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/" or path.startswith("/index.html"):
            self._send_html(INDEX_HTML)
            return
        if path == "/api/status":
            self._send_json(self._status_payload())
            return
        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            job = self.state.get_job(job_id)
            if not job:
                self._send_json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json(_job_to_dict(job))
            return
        if path == "/api/latest-report":
            self._send_json({"content": _read_latest_report(self.state.project_dir)})
            return
        if path == "/api/evidence":
            kind = (query.get("kind") or ["summary"])[0]
            self._send_json({"html": _render_evidence_html(self.state.project_dir, kind)})
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        """Start one fixed action from the browser."""

        if self.path != "/api/jobs":
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        payload = self._read_json()
        action = str(payload.get("action") or "")
        try:
            command = _action_to_command(action, payload, project_dir=self.state.project_dir)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        job = self.state.start_job(command)
        self._send_json(_job_to_dict(job), HTTPStatus.CREATED)

    def log_message(self, fmt: str, *args: Any) -> None:
        """Keep the terminal quiet unless the server itself fails."""

        return

    def _status_payload(self) -> dict[str, Any]:
        """Return lightweight project status for dashboard cards."""

        return {
            "project_dir": str(self.state.project_dir),
            "python": sys.version.split()[0],
            "latest_run": str(_latest_run_dir(self.state.project_dir) or ""),
            "latest_report": _latest_report_path(self.state.project_dir),
            "feedback": _latest_feedback_outcome(self.state.project_dir),
            "jobs": [_job_to_dict(job) for job in self.state.latest_jobs()],
        }

    def _read_json(self) -> dict[str, Any]:
        """Read a small JSON request body."""

        length = int(self.headers.get("Content-Length") or "0")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_html(self, text: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(text.encode("utf-8"))

    def _send_json(self, data: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def run_ui(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Start the local browser UI."""

    project_dir = _find_project_dir(Path.cwd())
    handler = type("BoundForgeUiHandler", (ForgeUiHandler,), {"state": UiState(project_dir)})
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}"
    print(f"Agent Forge UI: {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Agent Forge UI.")
    finally:
        server.server_close()


def build_ui_parser(parser: argparse.ArgumentParser) -> None:
    """Attach UI options to the public CLI."""

    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")


def run_ui_from_args(args: argparse.Namespace) -> None:
    """CLI adapter for ``forge ui``."""

    run_ui(host=args.host, port=args.port, open_browser=not args.no_open)


def _find_project_dir(start: Path) -> Path:
    """Find the repo root used by every UI action.

    Users often launch tools from a nested folder in PyCharm/VS Code. The UI is
    meant to remove entrypoint friction, so it should not depend on the current
    terminal already being at the project root.
    """

    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "agent_forge").is_dir():
            return candidate
    return current


def _action_to_command(
    action: str,
    payload: dict[str, Any],
    *,
    project_dir: Path | None = None,
) -> UiCommand:
    """Translate browser form settings into a bounded command.

    This is the key product boundary for the page UI. The browser can configure
    agent parameters, but it cannot submit arbitrary shell. Every action maps to
    one known ``agent_forge`` entrypoint plus validated flags.
    """

    python = sys.executable
    if action == "doctor":
        return UiCommand("Doctor", [python, "-m", "agent_forge", "doctor"])
    if action == "verify":
        return UiCommand("Verify", ["bash", "scripts/verify.sh"])
    if action == "agent_run":
        return _build_agent_run_command(python, payload)
    if action == "swebench_sample":
        return _build_swebench_command(python, payload, regression=False)
    if action == "swebench_regression":
        return _build_swebench_command(python, payload, regression=True)
    if action == "report":
        return UiCommand("Latest Report", [python, "-m", "agent_forge", "report", "latest"])
    if action == "replay":
        return UiCommand("Latest Replay", [python, "-m", "agent_forge", "replay", "latest"])
    if action == "feedback":
        if project_dir is None:
            raise ValueError("project directory is required for feedback")
        trace_path = _latest_trace_path(project_dir)
        if trace_path is None:
            raise ValueError("no trace artifact is available for feedback")
        outcome = _payload_choice(
            payload,
            "feedbackOutcome",
            {"accepted", "needs_work", "rejected"},
            "needs_work",
        )
        command = [
            python,
            "-m",
            "agent_forge",
            "eval",
            "feedback",
            str(trace_path),
            "--outcome",
            outcome,
            "--reviewer",
            "workbench",
        ]
        for label in _payload_csv(payload, "feedbackLabels"):
            command.extend(["--label", label])
        _append_optional(command, "--note", _payload_text(payload, "feedbackNote", ""))
        return UiCommand("Record Human Feedback", command, command[:])
    if action == "export_dataset":
        if project_dir is None:
            raise ValueError("project directory is required for dataset export")
        run_dir = _latest_run_dir(project_dir)
        if run_dir is None:
            raise ValueError("no run artifact is available for dataset export")
        command = [
            python,
            "-m",
            "agent_forge",
            "eval",
            "export-dataset",
            str(run_dir),
            "--output",
            ".agent_forge/evaluation/evidence_dataset.jsonl",
        ]
        if _payload_bool(payload, "requireFeedback", True):
            command.append("--require-feedback")
        return UiCommand("Export Evidence Dataset", command, command[:])
    raise ValueError(f"unsupported action: {action}")


def _build_agent_run_command(python: str, payload: dict[str, Any]) -> UiCommand:
    """Build the canonical repository-agent run from page settings."""

    task = _payload_text(
        payload,
        "task",
        "检查当前仓库结构，找出一个小而安全的改进点，完成修改并保留 trace 和 usage 证据。",
    )
    if len(task) < 6:
        raise ValueError("Task is too short. Describe what you want the agent to do.")
    provider = _payload_choice(payload, "provider", {"deepseek", "openai", "openai-compatible"}, "deepseek")
    command = [python, "-m", "agent_forge", "run", task]
    command.extend(["--workspace", _payload_text(payload, "workspace", ".")])
    command.extend(["--provider", provider])
    _append_optional(command, "--model", _payload_text(payload, "model", ""))
    _append_optional(command, "--base-url", _payload_text(payload, "baseUrl", ""))
    command.extend(["--max-steps", str(_payload_int(payload, "maxSteps", 16, 1, 80))])
    command.extend(["--max-context-chars", str(_payload_int(payload, "maxContextChars", 12000, 1000, 120000))])
    command.extend(
        [
            "--approval-mode",
            _payload_choice(payload, "approvalMode", {"trusted", "on-write", "on-risk", "locked", "dry-run"}, "trusted"),
        ]
    )
    execution_mode = _payload_choice(
        payload,
        "executionMode",
        {"local", "worktree", "container"},
        "worktree",
    )
    network_policy = _payload_choice(payload, "networkPolicy", {"deny", "allow"}, "deny")
    tool_routing = _payload_choice(payload, "toolRouting", {"task-aware", "all"}, "task-aware")
    if not _payload_bool(payload, "autoApproveWrites", False):
        command.append("--no-auto-approve-writes")
    command.extend(["--network-policy", network_policy, "--tool-routing", tool_routing])
    command.extend(["--output-root", _payload_text(payload, "outputRoot", ".agent_forge/runs")])
    agent_mode = _payload_choice(payload, "runAgentMode", {"single", "multi", "fanout"}, "single")
    command.extend(["--agent-mode", agent_mode])
    if agent_mode == "multi":
        command.extend(["--profile", "coding_fix", "--max-revision-rounds", "2"])
    elif agent_mode == "fanout":
        plan_path = _payload_project_path(
            payload,
            "fanoutPlan",
            "examples/fanout-plan.sample.json",
        )
        command.extend(["--fanout-plan", plan_path])
        resume_path = _payload_project_path(payload, "fanoutResume", "", required=False)
        if resume_path:
            command.extend(["--fanout-resume", resume_path])
        command.extend(
            [
                "--max-workers",
                str(_payload_int(payload, "fanoutMaxWorkers", 4, 1, 8)),
                "--execution-mode",
                "worktree",
                "--no-keep-worktree",
            ]
        )
    else:
        command.extend(["--execution-mode", execution_mode])
        command.append("--keep-worktree" if _payload_bool(payload, "keepWorktree", False) else "--no-keep-worktree")

    skills = _payload_text(payload, "skills", "auto")
    if skills:
        command.extend(["--skills", skills])
    for manifest in _payload_csv(payload, "skillManifests"):
        command.extend(["--skill-manifest", manifest])

    mcp_config = _payload_text(payload, "mcpConfig", "")
    if mcp_config:
        command.extend(["--mcp-config", mcp_config])
    for tool_name in _payload_csv(payload, "mcpTools"):
        command.extend(["--mcp-tool", tool_name])

    return UiCommand(
        title=f"Agent Run · {provider}",
        command=command,
        display_command=command[:],
        env=_api_key_env(payload, provider),
    )


def _build_swebench_command(python: str, payload: dict[str, Any], *, regression: bool) -> UiCommand:
    """Build a benchmark run from the same page-level model/runtime settings."""

    provider = _payload_choice(payload, "provider", {"deepseek", "openai", "openai-compatible"}, "deepseek")
    command = [python, "-m", "agent_forge", "bench", "swebench"]
    if regression:
        command.extend(["--regression-set", "core"])
    else:
        command.extend(["--showcase", "--limit", str(_payload_int(payload, "limit", 1, 1, 20))])
    command.extend(["--provider", provider])
    _append_optional(command, "--model", _payload_text(payload, "model", ""))
    _append_optional(command, "--base-url", _payload_text(payload, "baseUrl", ""))
    command.extend(["--max-steps", str(_payload_int(payload, "maxSteps", 40, 1, 80))])
    command.extend(["--max-context-chars", str(_payload_int(payload, "maxContextChars", 18000, 1000, 120000))])
    command.extend(["--output-root", _payload_text(payload, "outputRoot", ".agent_forge/runs")])
    command.extend(
        [
            "--execution-mode",
            _payload_choice(payload, "executionMode", {"local", "worktree", "container"}, "worktree"),
            "--network-policy",
            _payload_choice(payload, "networkPolicy", {"deny", "allow"}, "deny"),
            "--tool-routing",
            _payload_choice(payload, "toolRouting", {"task-aware", "all"}, "task-aware"),
        ]
    )
    command.append("--keep-worktree" if _payload_bool(payload, "keepWorktree", False) else "--no-keep-worktree")
    agent_mode = _payload_choice(payload, "benchAgentMode", {"single", "multi", "compare"}, "compare")
    command.extend(["--agent-mode", agent_mode, "--profile", "coding_fix", "--max-revision-rounds", "2"])
    if _payload_bool(payload, "directBaseline", True):
        command.append("--direct-baseline")
    if _payload_bool(payload, "officialEvaluate", False):
        command.append("--evaluate")
        command.extend(["--max-workers", str(_payload_int(payload, "maxWorkers", 1, 1, 8))])

    title = "SWE-bench Regression Set" if regression else "SWE-bench Reference Case"
    return UiCommand(
        title=f"{title} · {provider}",
        command=command,
        display_command=command[:],
        env=_api_key_env(payload, provider),
    )


def _payload_text(payload: dict[str, Any], key: str, default: str) -> str:
    """Read one string form value and trim accidental whitespace."""

    value = payload.get(key)
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _payload_project_path(
    payload: dict[str, Any],
    key: str,
    default: str,
    *,
    required: bool = True,
) -> str:
    """Accept only project-relative artifact paths from the browser."""

    text = str(payload.get(key) or default).strip().replace("\\", "/")
    if not text and not required:
        return ""
    path = PurePosixPath(text)
    if (
        not text
        or path.is_absolute()
        or ".." in path.parts
        or text.startswith("~")
        or (path.parts and ":" in path.parts[0])
    ):
        raise ValueError(f"{key} must be a relative project path")
    return path.as_posix()


def _payload_int(payload: dict[str, Any], key: str, default: int, min_value: int, max_value: int) -> int:
    """Read one bounded integer form value."""

    try:
        value = int(payload.get(key) or default)
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(value, max_value))


def _payload_bool(payload: dict[str, Any], key: str, default: bool) -> bool:
    """Read one checkbox-style value from JSON."""

    value = payload.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _payload_choice(payload: dict[str, Any], key: str, allowed: set[str], default: str) -> str:
    """Read a select value while rejecting unexpected actions."""

    value = _payload_text(payload, key, default)
    if value not in allowed:
        raise ValueError(f"Unsupported {key}: {value}")
    return value


def _payload_csv(payload: dict[str, Any], key: str) -> list[str]:
    """Read comma/newline-separated small lists from advanced fields."""

    raw = str(payload.get(key) or "")
    values = []
    for piece in raw.replace("\n", ",").split(","):
        value = piece.strip()
        if value:
            values.append(value)
    return values


def _append_optional(command: list[str], flag: str, value: str) -> None:
    """Append a CLI flag only when the page supplies a value."""

    if value:
        command.extend([flag, value])


def _api_key_env(payload: dict[str, Any], provider: str) -> dict[str, str]:
    """Pass a page-provided API key without exposing it in argv or artifacts."""

    api_key = str(payload.get("apiKey") or "").strip()
    if not api_key:
        return {}
    env = {"AGENT_FORGE_API_KEY": api_key}
    if provider == "deepseek":
        env["DEEPSEEK_API_KEY"] = api_key
    elif provider == "openai":
        env["OPENAI_API_KEY"] = api_key
    return env


def _job_to_dict(job: UiJob) -> dict[str, Any]:
    """Serialize one job for the browser."""

    return {
        "id": job.id,
        "title": job.title,
        "command": " ".join(job.display_command),
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "status": job.status,
        "exit_code": job.exit_code,
        "output": job.output,
    }


def _latest_report_path(project_dir: Path) -> str:
    """Return latest report path if available."""

    run_dir = _latest_run_dir(project_dir)
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


def _read_latest_report(project_dir: Path) -> str:
    """Read the latest report or return a friendly placeholder."""

    path = _latest_report_path(project_dir)
    if not path:
        return "No report yet. Run DeepSeek Agent Run or SWE-bench Sample first."
    return Path(path).read_text(encoding="utf-8")


def _render_evidence_html(project_dir: Path, kind: str) -> str:
    """Render human-facing evidence from trace/report artifacts.

    Raw trace JSON is excellent for replayability but painful to read during a
    workbench. These views intentionally summarize the same source artifacts into
    cards, tables, badges, and step timelines.
    """

    if kind == "summary":
        return _render_result_summary(project_dir)
    if kind == "usage":
        return _render_usage_dashboard(project_dir)
    if kind == "timeline":
        return _render_trace_timeline(project_dir)
    if kind == "evidence":
        return _render_run_evidence(project_dir)
    if kind == "compare":
        return _render_compare_dashboard(project_dir)
    if kind == "controls":
        return _render_runtime_controls(project_dir)
    if kind == "orchestration":
        return _render_orchestration_dashboard(project_dir)
    if kind == "evaluation":
        return _render_evaluation_dashboard(project_dir)
    if kind == "feedback":
        return _render_feedback_dashboard(project_dir)
    if kind == "raw_report":
        return f"<pre class='raw-text'>{_escape(_read_latest_report(project_dir))}</pre>"
    return _empty_evidence(f"Unsupported evidence view: {kind}")


def _latest_run_dir(project_dir: Path) -> Path | None:
    """Resolve the newest evidence run directory.

    `scripts/verify.sh` writes a lightweight smoke run under
    `.agent_forge/verify/runs` and updates `latest/run.txt`. That run is useful
    for health checks, but it does not contain SWE-bench comparison artifacts.
    UI evidence should follow the newest real run under `.agent_forge/runs`,
    whether it came from SWE-bench or a normal future Agent run.
    """

    latest = project_dir / ".agent_forge/latest"
    runs_dir = project_dir / ".agent_forge/runs"
    candidates: list[Path] = []
    bench_run = _run_dir_from_pointer(project_dir, latest / "bench.txt")
    if bench_run and _is_under(bench_run, runs_dir):
        candidates.append(bench_run)
    latest_run = _run_dir_from_pointer(project_dir, latest / "run.txt")
    if latest_run and _is_under(latest_run, runs_dir):
        candidates.append(latest_run)
    if runs_dir.exists():
        candidates.extend(path for path in runs_dir.iterdir() if path.is_dir())
    if candidates:
        unique = {path.resolve(): path for path in candidates}
        return max(unique.values(), key=lambda path: path.stat().st_mtime)
    return latest_run


def _run_dir_from_pointer(project_dir: Path, pointer: Path) -> Path | None:
    """Resolve one latest pointer, ignoring stale paths."""

    if not pointer.exists():
        return None
    run_dir = Path(pointer.read_text(encoding="utf-8").strip())
    if not run_dir.is_absolute():
        run_dir = project_dir / run_dir
    return run_dir if run_dir.exists() else None


def _is_under(path: Path, parent: Path) -> bool:
    """Return whether `path` is inside `parent`, tolerating missing parents."""

    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _latest_trace_path(project_dir: Path) -> Path | None:
    """Return the most relevant trace for the latest run."""

    run_dir = _latest_run_dir(project_dir)
    if not run_dir:
        return None
    direct = run_dir / "trace.json"
    if direct.exists():
        return direct
    traces = sorted(run_dir.glob("cases/**/trace.json"))
    return max(traces, key=lambda path: path.stat().st_mtime) if traces else None


def _latest_usage_path(project_dir: Path) -> Path | None:
    """Return the most relevant usage.json for the latest run."""

    run_dir = _latest_run_dir(project_dir)
    if not run_dir:
        return None
    direct = run_dir / "usage.json"
    if direct.exists():
        return direct
    usages = sorted(run_dir.glob("cases/**/usage.json"))
    return max(usages, key=lambda path: path.stat().st_mtime) if usages else None


def _latest_comparison_path(project_dir: Path) -> Path | None:
    """Return the newest single-vs-multi comparison artifact when present."""

    run_dir = _latest_run_dir(project_dir)
    if not run_dir:
        return None
    candidates = [run_dir / "comparison.json"]
    candidates.extend(sorted(run_dir.glob("cases/*/comparison.json")))
    candidates.extend(sorted(run_dir.glob("cases/*/*/comparison.json")))
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def _latest_multi_agent_summary_path(project_dir: Path) -> Path | None:
    """Return the newest multi-agent artifact summary when present."""

    run_dir = _latest_run_dir(project_dir)
    if not run_dir:
        return None
    candidates = [run_dir / "multi_agent/multi_agent_summary.json"]
    candidates.extend(sorted(run_dir.glob("cases/**/multi_agent/multi_agent_summary.json")))
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def _latest_fanout_summary_path(project_dir: Path) -> Path | None:
    """Return the live fanout summary for the latest normal run."""

    run_dir = _latest_run_dir(project_dir)
    if not run_dir:
        return None
    candidate = run_dir / "fanout" / "fanout_summary.json"
    return candidate if candidate.exists() else None


def _trace_paths_for_latest_run(project_dir: Path) -> list[tuple[str, Path]]:
    """Return display traces with multi-agent first and single-agent second."""

    run_dir = _latest_run_dir(project_dir)
    if run_dir is None:
        return []
    direct = run_dir / "trace.json"
    if direct.exists():
        return [("AgentLoop", direct)]
    traces = list(run_dir.glob("cases/**/trace.json"))

    def trace_order(path: Path) -> tuple[int, float]:
        parts = set(path.parts)
        if "multi" in parts:
            priority = 0
        elif "single" in parts:
            priority = 1
        else:
            priority = 2
        return priority, -path.stat().st_mtime

    ordered = sorted(traces, key=trace_order)
    labelled: list[tuple[str, Path]] = []
    seen_labels: set[str] = set()
    for path in ordered:
        scope = _trace_scope_label(path)
        label = "Multi-Agent Runtime" if "multi" in path.parts else "Single-Agent Runtime" if "single" in path.parts else scope
        if label in seen_labels:
            continue
        seen_labels.add(label)
        labelled.append((label, path))
    return labelled


def _latest_feedback_path(project_dir: Path) -> Path | None:
    """Locate feedback attached to the latest displayed trace or run."""

    trace_path = _latest_trace_path(project_dir)
    run_dir = _latest_run_dir(project_dir)
    candidates = []
    if trace_path is not None:
        candidates.append(trace_path.parent / "feedback.json")
    if run_dir is not None:
        candidates.append(run_dir / "feedback.json")
        candidates.extend(run_dir.glob("cases/**/feedback.json"))
    existing = [path for path in candidates if path.exists()]
    return max(existing, key=lambda path: path.stat().st_mtime) if existing else None


def _latest_feedback_outcome(project_dir: Path) -> str:
    """Return the latest human judgment without equating it to evaluation."""

    feedback = _read_json_file(_latest_feedback_path(project_dir))
    return str(feedback.get("outcome") or "unreviewed")


def _latest_result_record(project_dir: Path) -> dict[str, Any]:
    """Return the case result corresponding to the displayed latest run."""

    run_dir = _latest_run_dir(project_dir)
    if run_dir is None:
        return {}
    results = _read_json_file(run_dir / "results.json")
    case_results = results.get("case_results") or []
    return case_results[0] if case_results and isinstance(case_results[0], dict) else {}


def _latest_direct_baseline_record(project_dir: Path) -> dict[str, Any]:
    """Read the one-shot model baseline paired with the latest benchmark run."""

    run_dir = _latest_run_dir(project_dir)
    path = run_dir / "direct_baseline_predictions.jsonl" if run_dir is not None else None
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


def _event_list(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only typed trace-event objects from a loose JSON boundary."""

    return [event for event in trace.get("events") or [] if isinstance(event, dict)]


def _last_event(trace: dict[str, Any], *event_types: str) -> dict[str, Any]:
    """Return the newest trace event whose type matches one of the names."""

    allowed = set(event_types)
    for event in reversed(_event_list(trace)):
        if str(event.get("event_type") or "") in allowed:
            return event
    return {}


def _read_json_file(path: Path | None) -> dict[str, Any]:
    """Read JSON defensively for UI evidence rendering."""

    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": str(exc), "path": str(path)}


def _render_result_summary(project_dir: Path) -> str:
    """Render the latest run as a readable result card."""

    run_dir = _latest_run_dir(project_dir)
    if not run_dir:
        return _empty_evidence("No run artifacts yet. Run the fixed SWE-bench showcase first.")

    fanout_path = _latest_fanout_summary_path(project_dir)
    fanout = _read_json_file(fanout_path)
    if fanout:
        return _render_fanout_result_summary(fanout, fanout_path)

    results = _read_json_file(run_dir / "results.json")
    usage = _read_json_file(_latest_usage_path(project_dir))
    trace = _read_json_file(_latest_trace_path(project_dir))
    summary = usage.get("summary") or {}
    report_path = _latest_report_path(project_dir) or "not found"

    if results:
        cases = results.get("case_results") or []
        patch_count = sum(1 for case in cases if int(case.get("patch_chars") or 0) > 0)
        status_text = ", ".join(f"{case.get('instance_id')}: {case.get('status')}" for case in cases) or "no cases"
        case_rows = "".join(
            "<tr>"
            f"<td class='mono'>{_escape(case.get('instance_id', ''))}</td>"
            f"<td>{_escape(case.get('repo', ''))}</td>"
            f"<td>{_badge(case.get('status', ''), _tone_for_status(case.get('status', '')))}</td>"
            f"<td>{_badge(case.get('failure_class', 'unclassified'), _tone_for_status(case.get('failure_class', '')))}</td>"
            f"<td>{_badge(case.get('evaluation_status', ''), _tone_for_status(case.get('evaluation_status', '')))}</td>"
            f"<td>{int(case.get('patch_chars') or 0)}</td>"
            f"<td>{_escape(case.get('diagnosis', ''))}</td>"
            f"<td>{_escape((case.get('next_actions') or [''])[0])}</td>"
            "</tr>"
            for case in cases
        )
        case_rows_html = case_rows or "<tr><td colspan='8'>No cases</td></tr>"
        body = [
            "<h2>结果摘要：这次跑成什么样</h2>",
            "<p class='help strong'>这不是原始日志，而是从 results.json、usage.json、trace.json 提炼出的展示卡片。</p>",
            _metric_grid(
                [
                    ("Run", results.get("run_id", ""), "Benchmark run id", "neutral"),
                    ("Provider", f"{results.get('provider', '')}/{results.get('model') or 'default'}", "真实模型配置", "ok"),
                    ("Cases", str(len(cases)), "本次跑了几个 SWE-bench case", "neutral"),
                    ("Patch", f"{patch_count}/{len(cases)}", "是否产生候选 diff", "ok" if patch_count else "warn"),
                    ("Status", status_text, "agent 结束状态", _tone_for_status(status_text)),
                    ("Cost", f"${float(summary.get('estimated_cost_usd') or 0):.6f}", "DeepSeek 估算成本", "ok"),
                ]
            ),
            "<h3>Fixed Showcase Case</h3>",
            "<p>默认 reference case 固定为 <span class='mono'>astropy__astropy-12907</span>：真实 Astropy nested CompoundModel separability bug。它足够复杂，可以稳定暴露上下文检索、工具选择、循环控制、成本统计的改进效果。</p>",
            f"<p><span class='label'>Latest report</span><span class='mono'>{_escape(report_path)}</span></p>",
            "<h3>Cases</h3>",
            "<table><thead><tr><th>instance</th><th>repo</th><th>agent status</th><th>diagnosis class</th><th>eval status</th><th>patch chars</th><th>diagnosis</th><th>next action</th></tr></thead>"
            f"<tbody>{case_rows_html}</tbody></table>",
        ]
    else:
        body = [
            "<h2>结果摘要：这次跑成什么样</h2>",
            _metric_grid(
                [
                    ("Run", usage.get("run_id", ""), "Normal agent run", "neutral"),
                    ("Stop", usage.get("stop_reason", ""), "停止原因", _tone_for_status(usage.get("stop_reason", ""))),
                    ("LLM Calls", str(summary.get("llm_calls", 0)), "模型调用次数", "neutral"),
                    ("Tokens", str(summary.get("total_tokens", 0)), "总 token", "neutral"),
                    ("Cost", f"${float(summary.get('estimated_cost_usd') or 0):.6f}", "估算成本", "ok"),
                    ("Tools", f"{summary.get('tool_calls', 0)} calls", "工具调用", "neutral"),
                ]
            ),
            f"<p><span class='label'>Task</span>{_escape((usage.get('task') or trace.get('task') or '')[:800])}</p>",
        ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_usage_dashboard(project_dir: Path) -> str:
    """Render token/cost/context/tool efficiency as a readable dashboard."""

    fanout_path = _latest_fanout_summary_path(project_dir)
    fanout = _read_json_file(fanout_path)
    if fanout:
        return _render_fanout_usage_dashboard(fanout, fanout_path)

    usage_path = _latest_usage_path(project_dir)
    usage = _read_json_file(usage_path)
    if not usage:
        return _empty_evidence("No usage.json found. Run DeepSeek Agent or SWE-bench showcase first.")

    summary = usage.get("summary") or {}
    rows = []
    for step in usage.get("steps") or []:
        calls = step.get("llm_calls") or []
        prompt = sum(int(call.get("prompt_tokens") or 0) for call in calls)
        completion = sum(int(call.get("completion_tokens") or 0) for call in calls)
        cost = sum(float(call.get("estimated_cost_usd") or 0) for call in calls)
        actions = step.get("actions") or []
        action_text = ", ".join(
            f"{action.get('tool', 'tool')}:{'ok' if action.get('success') else 'fail'}"
            for action in actions[:4]
        )
        if len(actions) > 4:
            action_text += f", +{len(actions) - 4}"
        rows.append(
            "<tr>"
            f"<td>{int(step.get('step') or 0)}</td>"
            f"<td>{len(calls)}</td>"
            f"<td>{prompt}</td>"
            f"<td>{completion}</td>"
            f"<td>${cost:.6f}</td>"
            f"<td>{int((step.get('context') or {}).get('total_chars') or 0)}</td>"
            f"<td>{_escape(action_text or 'none')}</td>"
            "</tr>"
        )

    context_sections = (usage.get("context_breakdown") or {}).get("section_chars") or {}
    context_rows = "".join(
        f"<tr><td>{_escape(name)}</td><td>{int(value)}</td></tr>"
        for name, value in sorted(context_sections.items(), key=lambda item: int(item[1]), reverse=True)
    )
    tools = ((usage.get("tool_efficiency") or {}).get("by_tool") or {})
    tool_rows = "".join(
        "<tr>"
        f"<td>{_escape(name)}</td>"
        f"<td>{data.get('calls', 0)}</td>"
        f"<td>{data.get('success', 0)}</td>"
        f"<td>{data.get('failed', 0)}</td>"
        f"<td>{int(data.get('duration_ms', 0) or 0)}</td>"
        "</tr>"
        for name, data in tools.items()
    )
    step_rows_html = "".join(rows) or "<tr><td colspan='7'>No step data</td></tr>"
    context_rows_html = context_rows or "<tr><td colspan='2'>No context data</td></tr>"
    tool_rows_html = tool_rows or "<tr><td colspan='5'>No tool data</td></tr>"

    body = [
        "<h2>成本与工具效率：工程量化证据</h2>",
        "<p class='help strong'>这里回答一次真实运行花了多少 token、多少钱、哪里消耗上下文，以及工具调用是否高效。</p>",
        _metric_grid(
            [
                ("LLM Calls", str(summary.get("llm_calls", 0)), "模型调用轮数", "neutral"),
                ("Total Tokens", str(summary.get("total_tokens", 0)), "input + output", "neutral"),
                ("Cache Hit", f"{float(summary.get('cache_hit_rate') or 0):.2%}", "缓存命中率", "ok"),
                ("Cost", f"${float(summary.get('estimated_cost_usd') or 0):.6f}", "估算成本", "ok"),
                ("Latency", f"{int(summary.get('llm_latency_ms') or 0)} ms", "模型总延迟", "neutral"),
                ("Tool Failures", str(summary.get("failed_tool_calls", 0)), "失败工具调用", "bad" if summary.get("failed_tool_calls") else "ok"),
            ]
        ),
        "<h3>Step Cost Breakdown</h3>",
        "<table><thead><tr><th>step</th><th>llm calls</th><th>input</th><th>output</th><th>cost</th><th>context chars</th><th>actions</th></tr></thead>"
        f"<tbody>{step_rows_html}</tbody></table>",
        "<div class='split'>",
        "<div><h3>Context Breakdown</h3><table><thead><tr><th>section</th><th>chars</th></tr></thead>"
        f"<tbody>{context_rows_html}</tbody></table></div>",
        "<div><h3>Tool Efficiency</h3><table><thead><tr><th>tool</th><th>calls</th><th>ok</th><th>fail</th><th>ms</th></tr></thead>"
        f"<tbody>{tool_rows_html}</tbody></table></div>",
        "</div>",
        f"<p><span class='label'>usage.json</span><span class='mono'>{_escape(str(usage_path))}</span></p>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_fanout_result_summary(fanout: dict[str, Any], path: Path | None) -> str:
    """Render task and merge outcomes for one live fanout run."""

    metrics = fanout.get("metrics") or {}
    rows = "".join(
        "<tr>"
        f"<td class='mono'>{_escape(result.get('task_id', ''))}</td>"
        f"<td>{_badge(str(result.get('status', '')), _tone_for_status(str(result.get('status', ''))))}</td>"
        f"<td>{_escape(result.get('resumed', False))}</td>"
        f"<td>{_escape(result.get('touched_files', []))}</td>"
        "</tr>"
        for result in fanout.get("results") or []
    ) or "<tr><td colspan='4'>No task results</td></tr>"
    body = [
        "<h2>Live Fanout 结果摘要</h2>",
        "<p class='help strong'>独立 AgentLoop workers、scope gate、确定性合并和 finalizer 的终态证据。</p>",
        _metric_grid(
            [
                ("Status", str(fanout.get("status", "")), "coordinator status", _tone_for_status(str(fanout.get("status", "")))),
                ("Tasks", str(metrics.get("task_count", 0)), "validated DAG tasks", "neutral"),
                ("Completed", str(metrics.get("completed_count", 0)), "accepted workers", "ok"),
                ("Max Workers", str(metrics.get("max_workers", 0)), "concurrency bound", "neutral"),
                ("Wall Time", f"{int(metrics.get('wall_time_ms') or 0)} ms", "includes finalizer", "neutral"),
                ("Decision", str(fanout.get("final_decision") or "not_run"), "isolated verifier", _tone_for_status(str(fanout.get("final_decision") or ""))),
            ]
        ),
        f"<p><span class='label'>Goal</span>{_escape(fanout.get('goal', ''))}</p>",
        f"<p><span class='label'>Batches</span><span class='mono'>{_escape(fanout.get('batches', []))}</span></p>",
        "<table><thead><tr><th>task</th><th>status</th><th>resumed</th><th>touched files</th></tr></thead>",
        f"<tbody>{rows}</tbody></table>",
        "<p class='help'>A merged candidate and FanoutVerifier PASS are runtime evidence, not official benchmark resolution.</p>",
        f"<p><span class='label'>fanout_summary.json</span><span class='mono'>{_escape(str(path or 'not found'))}</span></p>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_fanout_usage_dashboard(fanout: dict[str, Any], path: Path | None) -> str:
    """Render aggregate worker plus finalizer usage for live fanout."""

    metrics = fanout.get("metrics") or {}
    body = [
        "<h2>Live Fanout 成本与并发证据</h2>",
        "<p class='help strong'>总模型指标包含 workers 与 finalizer；worker time 和 wall time 分开显示。</p>",
        _metric_grid(
            [
                ("LLM Calls", str(metrics.get("llm_calls", 0)), "this run", "neutral"),
                ("Total Tokens", str(metrics.get("total_tokens", 0)), "this run", "neutral"),
                ("Cost", f"${float(metrics.get('estimated_cost_usd') or 0):.6f}", "this run estimate", "ok"),
                ("Wall Time", f"{int(metrics.get('wall_time_ms') or 0)} ms", "end-to-end", "neutral"),
                ("Current Worker", f"{int(metrics.get('current_worker_duration_ms') or 0)} ms", "this run only", "neutral"),
                ("Recovered Worker", f"{int(metrics.get('resumed_worker_duration_ms') or 0)} ms", "historical artifacts", "neutral"),
                ("Max Workers", str(metrics.get("max_workers", 0)), "configured bound", "neutral"),
                ("Tool Calls", str(metrics.get("tool_calls", 0)), "worker and verifier tools", "neutral"),
                ("Tool Failures", str(metrics.get("failed_tool_calls", 0)), "failed observations", "bad" if metrics.get("failed_tool_calls") else "ok"),
            ]
        ),
        f"<p><span class='label'>fanout_summary.json</span><span class='mono'>{_escape(str(path or 'not found'))}</span></p>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_trace_timeline(project_dir: Path) -> str:
    """Render multi-agent and single-agent traces in an explicit order."""

    trace_entries = _trace_paths_for_latest_run(project_dir)
    if not trace_entries:
        return _empty_evidence("No trace.json found. Run DeepSeek Agent or SWE-bench showcase first.")
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>OBSERVABILITY</span><h2>Execution Timeline</h2></div>"
        "<span class='claim-note'>Multi first, Single second</span></div>",
        "<div class='legend-row'>"
        "<span class='legend-item blue'>model / plan</span>"
        "<span class='legend-item purple'>context / routing</span>"
        "<span class='legend-item ok'>tool / check passed</span>"
        "<span class='legend-item bad'>failed</span>"
        "<span class='legend-item neutral'>state</span>"
        "</div>",
    ]
    for label, trace_path in trace_entries:
        body.append(_render_trace_lane(label, trace_path))
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_trace_lane(label: str, trace_path: Path) -> str:
    """Render one trace without forcing readers to open its JSON."""

    trace = _read_json_file(trace_path)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for event in _event_list(trace):
        grouped.setdefault(int(event.get("step") or 0), []).append(event)
    step_blocks = []
    for step, events in sorted(grouped.items()):
        failures = sum(1 for event in events if not bool(event.get("success", True)))
        pills = "".join(
            f"<span class='event-pill {_event_tone(str(event.get('event_type') or ''), bool(event.get('success', True)))}'>"
            f"{_escape(_format_trace_event_label(index, event))}</span>"
            for index, event in enumerate(events, start=1)
        )
        step_blocks.append(
            "<div class='timeline-step'>"
            f"<div class='timeline-head'><strong>Step {step}</strong>"
            f"{_badge(str(failures) + ' failed', 'bad') if failures else _badge('passed', 'ok')}</div>"
            f"<div>{pills}</div></div>"
        )
    events = _event_list(trace)
    return (
        "<section class='evidence-section timeline-lane'>"
        f"<div class='section-title'><h3>{_escape(label)}</h3>{_badge(str(trace.get('stop_reason') or 'unknown'), _tone_for_status(str(trace.get('stop_reason') or '')))}</div>"
        f"<div class='run-facts'><span>run <b class='mono'>{_escape(trace.get('run_id', ''))}</b></span>"
        f"<span>{len(grouped)} steps</span><span>{len(events)} events</span></div>"
        f"{''.join(step_blocks)}"
        f"<details class='provenance'><summary>Trace provenance</summary><code>{_escape(str(trace_path))}</code></details>"
        "</section>"
    )


def _render_run_evidence(project_dir: Path) -> str:
    """Render one evidence chain from task input to bounded claim."""

    run_dir = _latest_run_dir(project_dir)
    comparison_path = _latest_comparison_path(project_dir)
    multi_path = _latest_multi_agent_summary_path(project_dir)
    usage_path = _latest_usage_path(project_dir)
    trace_path = _latest_trace_path(project_dir)

    comparison = _read_json_file(comparison_path)
    multi = _read_json_file(multi_path)
    usage = _read_json_file(usage_path)
    trace = _read_json_file(trace_path)
    summary = usage.get("summary") or {}

    single_status = comparison.get("single_status") or "-"
    multi_status = comparison.get("multi_status") or multi.get("status") or "-"
    failure = comparison.get("failure_taxonomy") or "unclassified"
    cost = float(summary.get("estimated_cost_usd") or 0.0)
    task_id = comparison.get("task_id") or multi.get("task") or trace.get("task") or "latest local run"
    revision_rounds = comparison.get("revision_rounds", multi.get("revision_rounds", 0))

    result = _latest_result_record(project_dir)
    evaluation_status = str(result.get("evaluation_status") or "not_evaluated")
    patch_chars = int(result.get("patch_chars") or 0)
    feedback_outcome = _latest_feedback_outcome(project_dir)
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>RUN EVIDENCE</span><h2>Runtime Evidence Overview</h2></div>"
        f"{_badge(evaluation_status, _tone_for_status(evaluation_status))}</div>",
        _metric_grid(
            [
                ("Case", str(task_id)[:90], "latest evidence target", "neutral"),
                ("Single", str(single_status), "canonical AgentLoop", _tone_for_status(str(single_status))),
                ("Multi", str(multi_status), "coordinator outcome", _tone_for_status(str(multi_status))),
                ("Revision", str(revision_rounds), "bounded review rounds", "neutral"),
                ("Cost", f"${cost:.6f}", "latest measured usage", "ok"),
                ("Failure Class", str(failure), "ordered diagnosis", _tone_for_status(str(failure))),
            ]
        ),
        "<section class='evidence-section'><div class='section-title'><h3>Runtime Pipeline</h3><span>policy outside prompt</span></div>",
        "<div class='pipeline'>"
        "<div><b>01</b><span>Context</span><small>selection + compression</small></div>"
        "<div><b>02</b><span>Model</span><small>plan + tool intent</small></div>"
        "<div><b>03</b><span>Control</span><small>routing + policy + approval</small></div>"
        "<div><b>04</b><span>Execution</span><small>sandbox + recovery</small></div>"
        "<div><b>05</b><span>Evidence</span><small>trace + usage + artifacts</small></div>"
        "<div><b>06</b><span>Evaluation</span><small>diagnosis + feedback</small></div>"
        "</div></section>",
        "<section class='evidence-section'><div class='section-title'><h3>Claim Ladder</h3><span>strongest supported statement</span></div>",
        "<div class='claim-ladder'>",
        _claim_step("Candidate patch", "present" if patch_chars else "absent", f"{patch_chars} chars", "ok" if patch_chars else "neutral"),
        _claim_step("Role verification", str(comparison.get("verifier_status") or "not_observed"), "runtime verifier, not official eval", _tone_for_status(str(comparison.get("verifier_status") or ""))),
        _claim_step("Official evaluation", evaluation_status, "authoritative benchmark boundary", _tone_for_status(evaluation_status)),
        _claim_step("Human feedback", feedback_outcome, "operator judgment", _tone_for_status(feedback_outcome)),
        "</div></section>",
        "<section class='evidence-section'><div class='section-title'><h3>Role Decisions</h3><span>artifact-mediated handoff</span></div>",
        "<table><thead><tr><th>role</th><th>decision</th><th>round</th><th>evidence excerpt</th></tr></thead>"
        f"<tbody>{_render_role_decision_rows(multi)}</tbody></table></section>",
        "<section class='evidence-section'><div class='section-title'><h3>Produced Artifacts</h3><span>content first, provenance second</span></div>",
        _render_artifact_cards(multi),
        "</section>",
        "<details class='provenance'><summary>Artifact provenance</summary>"
        f"<code>{_escape(str(run_dir or 'not found'))}</code><code>{_escape(str(comparison_path or 'not found'))}</code>"
        f"<code>{_escape(str(trace_path or 'not found'))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _claim_step(title: str, state: str, detail: str, tone: str) -> str:
    """Render one rung without promoting weaker evidence into a solved claim."""

    return (
        f"<div class='claim-step {tone}'><span>{_escape(title)}</span>"
        f"<strong>{_escape(state)}</strong><small>{_escape(detail)}</small></div>"
    )


def _render_role_decision_rows(summary: dict[str, Any]) -> str:
    """Show role conclusions directly instead of artifact file names."""

    rows = []
    for result in summary.get("role_results") or []:
        excerpt = str(result.get("final_answer") or result.get("output") or "")
        excerpt = " ".join(excerpt.replace("#", " ").split())[:360]
        decision = str(result.get("decision") or result.get("status") or "-")
        rows.append(
            "<tr>"
            f"<td><b>{_escape(result.get('role') or result.get('name') or '-')}</b></td>"
            f"<td>{_badge(decision, _tone_for_status(decision))}</td>"
            f"<td>{_escape(result.get('round_index', 0))}</td>"
            f"<td>{_escape(excerpt or '-')}</td>"
            "</tr>"
        )
    return "".join(rows) or "<tr><td colspan='4'>No role decisions were observed in this run.</td></tr>"


def _render_artifact_cards(summary: dict[str, Any]) -> str:
    """Render artifact content and handoff semantics with paths hidden by default."""

    artifacts = summary.get("artifacts") or []
    if not isinstance(artifacts, list) or not artifacts:
        return "<div class='empty-inline'>No multi-agent artifacts were produced in this run.</div>"
    consumers = {
        "Implementer": "Reviewer",
        "Reviewer": "Coordinator + Verifier",
        "Verifier": "Coordinator",
        "Coordinator": "Run result",
    }
    cards = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        role = str(artifact.get("role") or "Unknown")
        path = Path(str(artifact.get("path") or ""))
        content = ""
        if path.exists() and path.is_file():
            content = path.read_text(encoding="utf-8", errors="replace")
        excerpt = " ".join((content or str(artifact.get("summary") or "")).replace("#", " ").split())[:460]
        cards.append(
            "<article class='artifact-card'>"
            f"<div class='artifact-head'><div><span>{_escape(role)}</span><h4>{_escape(artifact.get('kind') or artifact.get('id') or 'artifact')}</h4></div>"
            f"{_badge('round ' + str(artifact.get('round_index', 0)), 'neutral')}</div>"
            f"<p>{_escape(excerpt or 'No content summary available.')}</p>"
            f"<div class='artifact-handoff'><span>producer <b>{_escape(role)}</b></span>"
            f"<span>consumer <b>{_escape(consumers.get(role, 'next stage'))}</b></span></div>"
            f"<details><summary>Source</summary><code>{_escape(str(path) if path else 'not found')}</code></details>"
            "</article>"
        )
    return "<div class='artifact-grid'>" + "".join(cards) + "</div>"


def _render_runtime_controls(project_dir: Path) -> str:
    """Render controls actually observed in the latest trace."""

    trace_path = _latest_trace_path(project_dir)
    trace = _read_json_file(trace_path)
    if not trace:
        return _empty_evidence("No trace evidence is available for runtime controls.")
    events = _event_list(trace)
    checkpoint = _last_event(trace, "task_state_checkpoint")
    task_state_value = checkpoint.get("task_state")
    task_state: dict[str, Any] = task_state_value if isinstance(task_state_value, dict) else {}
    metadata_value = task_state.get("metadata")
    metadata: dict[str, Any] = metadata_value if isinstance(metadata_value, dict) else {}
    environment_value = metadata.get("execution_environment")
    environment: dict[str, Any] = environment_value if isinstance(environment_value, dict) else {}
    context_event = _last_event(trace, "context_assembly")
    context_value = context_event.get("context")
    context_snapshot: dict[str, Any] = context_value if isinstance(context_value, dict) else context_event
    routing_value = context_snapshot.get("tool_routing")
    routing: dict[str, Any] = routing_value if isinstance(routing_value, dict) else {}
    allowed = routing.get("allowed_tools") or []
    hidden = routing.get("dropped_tools") or routing.get("hidden_tools") or []
    permission_events = [event for event in events if event.get("event_type") == "permission_check"]
    decisions = {"allow": 0, "ask": 0, "deny": 0}
    for event in permission_events:
        permission_value = event.get("permission")
        permission: dict[str, Any] = permission_value if isinstance(permission_value, dict) else {}
        decision = str(
            event.get("permission_decision")
            or event.get("decision")
            or permission.get("decision")
            or ""
        ).lower()
        if decision in decisions:
            decisions[decision] += 1
    checkpoints = sum(1 for event in events if event.get("event_type") == "task_state_checkpoint")
    human_events = sum(1 for event in events if "human" in str(event.get("event_type") or ""))
    recovery_events = sum(1 for event in events if "recovery" in str(event.get("event_type") or ""))
    operation_events = sum(1 for event in events if "operation" in str(event.get("event_type") or ""))
    skill_event = _last_event(trace, "skill_selection")
    active_skills = context_snapshot.get("active_skills") or skill_event.get("selected_skills") or skill_event.get("skills") or []
    mcp_tools = [str(tool) for tool in allowed if "." in str(tool)]
    mode = str(environment.get("mode") or "not_observed")
    network = str(environment.get("network_policy") or "not_observed")
    intervention_rows = []
    for event in events:
        event_type = str(event.get("event_type") or "")
        permission_decision = str(event.get("permission_decision") or "")
        if event_type == "permission_check" and permission_decision not in {"ask", "deny"}:
            continue
        if event_type not in {"permission_check", "human_approval", "recovery_decision"}:
            continue
        state = permission_decision or str(event.get("observation") or event.get("failure_kind") or "observed")
        evidence = str(
            event.get("reason")
            or event.get("recovery_hint")
            or event.get("observation")
            or ""
        )
        intervention_rows.append(
            "<tr>"
            f"<td>{_escape(event.get('step', 0))}</td>"
            f"<td>{_escape(event.get('agent_name') or '-')}</td>"
            f"<td>{_escape(event_type)}</td>"
            f"<td>{_badge(state, _tone_for_status(state))}</td>"
            f"<td>{_escape(event.get('tool_call') or '-')}</td>"
            f"<td>{_escape(evidence)}</td>"
            "</tr>"
        )
    intervention_html = "".join(intervention_rows) or "<tr><td colspan='6'>No approval or recovery intervention was observed.</td></tr>"
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>CONTROL PLANE</span><h2>Runtime Controls</h2></div>"
        f"{_badge(mode, _tone_for_status(mode))}</div>",
        _metric_grid(
            [
                ("Environment", mode, "local / worktree / container", "neutral"),
                ("Network", network, "execution boundary", "ok" if network == "deny" else "warn"),
                ("Tool Surface", f"{len(allowed)} visible", f"{len(hidden)} hidden", "neutral"),
                ("Permissions", f"{decisions['allow']} / {decisions['ask']} / {decisions['deny']}", "allow / ask / deny", "neutral"),
                ("Checkpoints", str(checkpoints), "durable task state", "ok" if checkpoints else "neutral"),
                ("HITL / Recovery", f"{human_events} / {recovery_events}", "observed trace events", "neutral"),
            ]
        ),
        "<section class='evidence-section'><div class='section-title'><h3>Enforced Boundaries</h3><span>observed, not inferred</span></div>",
        "<table><thead><tr><th>control</th><th>latest evidence</th><th>runtime owner</th></tr></thead><tbody>",
        f"<tr><td>Execution isolation</td><td>{_escape(environment.get('active_workspace') or mode)}</td><td>ExecutionEnvironment</td></tr>",
        f"<tr><td>Network policy</td><td>{_escape(network)}</td><td>ExecutionEnvironment + CommandPolicy</td></tr>",
        f"<tr><td>Workspace writes</td><td>{_escape(context_snapshot.get('permission_summary') or 'not observed')}</td><td>WorkspaceSandbox + PermissionPolicy</td></tr>",
        f"<tr><td>Tool visibility</td><td>{_escape(', '.join(str(item) for item in allowed) or 'not observed')}</td><td>ToolRouter</td></tr>",
        f"<tr><td>Hidden tools</td><td>{_escape(', '.join(str(item) for item in hidden) or 'none observed')}</td><td>ToolRouter</td></tr>",
        f"<tr><td>Skill injection</td><td>{_escape(active_skills or 'not observed')}</td><td>SkillRegistry + ContextStrategy</td></tr>",
        f"<tr><td>MCP exposure</td><td>{_escape(', '.join(mcp_tools) or 'none observed')}</td><td>MCP adapter + ToolRegistry</td></tr>",
        f"<tr><td>Human control barrier</td><td>{human_events} observed events</td><td>HumanInputStore + ApprovalStore</td></tr>",
        f"<tr><td>Idempotent writes</td><td>{operation_events} operation events</td><td>OperationLedger</td></tr>",
        f"<tr><td>Typed evidence contract</td><td>TraceEvent envelope + named task checkpoint</td><td>TraceRecorder + TaskCheckpoint</td></tr>",
        "</tbody></table></section>",
        "<section class='evidence-section'><div class='section-title'><h3>Durability & Human Control</h3><span>latest-run event coverage</span></div>",
        "<div class='capability-strip'>"
        f"<div><b>{checkpoints}</b><span>state checkpoints</span></div>"
        f"<div><b>{human_events}</b><span>human-input events</span></div>"
        f"<div><b>{recovery_events}</b><span>recovery decisions</span></div>"
        f"<div><b>{len(permission_events)}</b><span>permission checks</span></div>"
        "</div><p class='boundary-note'>Zero means the capability was not exercised by this run; it does not fabricate a pass.</p></section>",
        "<section class='evidence-section'><div class='section-title'><h3>Interventions</h3><span>approval and recovery evidence</span></div>"
        "<table><thead><tr><th>step</th><th>agent</th><th>event</th><th>state</th><th>tool</th><th>evidence</th></tr></thead>"
        f"<tbody>{intervention_html}</tbody></table></section>",
        f"<details class='provenance'><summary>Control provenance</summary><code>{_escape(str(trace_path))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_orchestration_dashboard(project_dir: Path) -> str:
    """Render sequential roles or live fanout without conflating the two."""

    fanout_path = _latest_fanout_summary_path(project_dir)
    fanout = _read_json_file(fanout_path)
    if fanout:
        return _render_fanout_result_summary(fanout, fanout_path)
    summary_path = _latest_multi_agent_summary_path(project_dir)
    summary = _read_json_file(summary_path)
    if not summary:
        return _empty_evidence("No orchestration artifact is available in the latest run.")
    decisions = summary.get("role_results") or []
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>ORCHESTRATION</span><h2>Multi-Agent Coordination</h2></div>"
        f"{_badge(str(summary.get('status') or 'unknown'), _tone_for_status(str(summary.get('status') or '')))}</div>",
        _metric_grid(
            [
                ("Mode", "sequential roles", "artifact-mediated", "neutral"),
                ("Roles", str(len(decisions)), "implement / review / verify", "neutral"),
                ("Revisions", str(summary.get("revision_rounds", 0)), "bounded loop", "neutral"),
                ("Artifacts", str(len(summary.get("artifacts") or [])), "explicit handoffs", "ok"),
            ]
        ),
        "<section class='evidence-section'><div class='section-title'><h3>Coordination Graph</h3><span>sequential path</span></div>"
        "<div class='coordination-graph'><div><b>Implementer</b><span>candidate + evidence</span></div>"
        "<i>artifact</i><div><b>Reviewer</b><span>risk + revision decision</span></div>"
        "<i>artifact</i><div><b>Verifier</b><span>independent validation</span></div>"
        "<i>verdict</i><div><b>Coordinator</b><span>finish or revise</span></div></div></section>",
        "<section class='evidence-section'><div class='section-title'><h3>Role Outcomes</h3><span>decisions and evidence</span></div>"
        "<table><thead><tr><th>role</th><th>decision</th><th>round</th><th>evidence excerpt</th></tr></thead>"
        f"<tbody>{_render_role_decision_rows(summary)}</tbody></table></section>",
        "<section class='evidence-section'><div class='section-title'><h3>Artifact Handoffs</h3><span>content visible in place</span></div>"
        f"{_render_artifact_cards(summary)}</section>",
        "<section class='evidence-section'><div class='section-title'><h3>Execution Models</h3><span>support is distinct from latest-run evidence</span></div>"
        "<table><thead><tr><th>mode</th><th>latest run</th><th>runtime contract</th></tr></thead><tbody>"
        "<tr><td>Single Agent</td><td>observed in paired comparison</td><td>canonical AgentLoop, lowest coordination overhead</td></tr>"
        "<tr><td>Sequential Multi-Agent</td><td>observed</td><td>role isolation, artifact handoff, bounded revision</td></tr>"
        "<tr><td>Live Fanout</td><td>supported, not exercised by this run</td><td>validated DAG, worktree workers, scope gate, deterministic merge, isolated finalizer, selective recovery</td></tr>"
        "</tbody></table></section>",
        f"<details class='provenance'><summary>Orchestration provenance</summary><code>{_escape(str(summary_path))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_evaluation_dashboard(project_dir: Path) -> str:
    """Render evaluation claims, comparison, and failure diagnosis together."""

    result = _latest_result_record(project_dir)
    comparison = _read_json_file(_latest_comparison_path(project_dir))
    direct_baseline = _latest_direct_baseline_record(project_dir)
    baseline_patch = str(direct_baseline.get("model_patch") or "")
    evaluation_status = str(result.get("evaluation_status") or "not_evaluated")
    diagnosis = str(result.get("diagnosis") or "No diagnosis artifact was found.")
    evidence = result.get("diagnosis_evidence") or []
    next_actions = result.get("next_actions") or []
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>EVALUATION</span><h2>Evidence & Claim Boundary</h2></div>"
        f"{_badge(evaluation_status, _tone_for_status(evaluation_status))}</div>",
        _metric_grid(
            [
                ("Result", str(result.get("status") or "unknown"), "agent outcome", _tone_for_status(str(result.get("status") or ""))),
                ("Failure Class", str(result.get("failure_class") or "unclassified"), "ordered taxonomy", _tone_for_status(str(result.get("failure_class") or ""))),
                ("Patch", str(result.get("patch_chars") or 0), "candidate characters", "ok" if result.get("patch_chars") else "neutral"),
                ("Official Eval", evaluation_status, "benchmark authority", _tone_for_status(evaluation_status)),
                ("Verifier", str(comparison.get("verifier_status") or "not_observed"), "runtime role", _tone_for_status(str(comparison.get("verifier_status") or ""))),
                ("Direct Baseline", str(len(baseline_patch)) if direct_baseline else "not_run", "one-shot patch chars", "neutral"),
            ]
        ),
        "<section class='evidence-section'><div class='section-title'><h3>Diagnosis</h3><span>why this status occurred</span></div>"
        f"<p class='diagnosis'>{_escape(diagnosis)}</p>"
        f"<div class='evidence-list'>{''.join(f'<span>{_escape(item)}</span>' for item in evidence)}</div></section>",
        "<section class='evidence-section'><div class='section-title'><h3>Next Evidence</h3><span>required before stronger claims</span></div>"
        f"<ol class='next-actions'>{''.join(f'<li>{_escape(item)}</li>' for item in next_actions) or '<li>No next action recorded.</li>'}</ol></section>",
        "<section class='evidence-section'><div class='section-title'><h3>Matched Comparison</h3><span>same task, different runtime design</span></div>"
        "<table><thead><tr><th>metric</th><th>single</th><th>multi</th></tr></thead><tbody>"
        f"<tr><td>status</td><td>{_escape(comparison.get('single_status', '-'))}</td><td>{_escape(comparison.get('multi_status', '-'))}</td></tr>"
        f"<tr><td>LLM calls</td><td>{_escape(comparison.get('single_llm_calls', '-'))}</td><td>{_escape(comparison.get('multi_llm_calls', '-'))}</td></tr>"
        f"<tr><td>tool calls</td><td>{_escape(comparison.get('single_tool_calls', '-'))}</td><td>{_escape(comparison.get('multi_tool_calls', '-'))}</td></tr>"
        f"<tr><td>cost</td><td>{_format_optional_cost(comparison.get('single_cost_usd'))}</td><td>{_format_optional_cost(comparison.get('multi_cost_usd'))}</td></tr>"
        f"<tr><td>patch generated</td><td>{_escape(comparison.get('single_patch_generated', '-'))}</td><td>{_escape(comparison.get('multi_patch_generated', '-'))}</td></tr>"
        "</tbody></table>"
        f"<p class='boundary-note'>{_escape(comparison.get('recommendation') or 'No comparison recommendation was recorded.')}</p></section>",
        "<section class='evidence-section'><div class='section-title'><h3>Harness vs Direct Model</h3><span>one-shot baseline boundary</span></div>"
        "<table><thead><tr><th>surface</th><th>direct model</th><th>governed runtime</th></tr></thead><tbody>"
        f"<tr><td>candidate patch</td><td>{len(baseline_patch) if direct_baseline else 'not run'} chars</td><td>{_escape(result.get('patch_chars') or 0)} chars</td></tr>"
        "<tr><td>tool execution</td><td>none</td><td>routed, policy checked, traced</td></tr>"
        "<tr><td>validation evidence</td><td>not collected by one-shot baseline</td><td>tool observations + verifier artifacts</td></tr>"
        "<tr><td>claim</td><td>candidate text only</td><td>candidate plus bounded runtime evidence</td></tr>"
        "</tbody></table><p class='boundary-note'>Patch length is not quality. Official evaluation remains the common authority for both variants.</p></section>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_feedback_dashboard(project_dir: Path) -> str:
    """Render the real human-feedback and privacy-conscious export state."""

    feedback_path = _latest_feedback_path(project_dir)
    feedback = _read_json_file(feedback_path)
    dataset_path = project_dir / ".agent_forge/evaluation/evidence_dataset.jsonl"
    records = 0
    if dataset_path.exists():
        records = sum(1 for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip())
    outcome = str(feedback.get("outcome") or "unreviewed")
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>IMPROVEMENT LOOP</span><h2>Feedback & Dataset</h2></div>"
        f"{_badge(outcome, _tone_for_status(outcome))}</div>",
        _metric_grid(
            [
                ("Human Outcome", outcome, "operator label", _tone_for_status(outcome)),
                ("Labels", str(len(feedback.get("labels") or [])), "curation metadata", "neutral"),
                ("Dataset Records", str(records), "safe JSONL projection", "neutral"),
                ("Patch Content", "excluded", "default export", "ok"),
            ]
        ),
        "<section class='evidence-section'><div class='section-title'><h3>Latest Human Judgment</h3><span>not benchmark authority</span></div>"
        "<table><tbody>"
        f"<tr><td>outcome</td><td>{_escape(outcome)}</td></tr>"
        f"<tr><td>labels</td><td>{_escape(', '.join(str(item) for item in feedback.get('labels') or []) or '-')}</td></tr>"
        f"<tr><td>note</td><td>{_escape(feedback.get('note') or '-')}</td></tr>"
        f"<tr><td>reviewer</td><td>{_escape(feedback.get('reviewer') or '-')}</td></tr>"
        "</tbody></table></section>",
        "<section class='evidence-section'><div class='section-title'><h3>Export Contract</h3><span>privacy-conscious by default</span></div>"
        "<table><thead><tr><th>included</th><th>excluded by default</th><th>provenance</th></tr></thead><tbody>"
        "<tr><td>task, stop reason, failure class, eval status</td><td>raw tool arguments and observations</td><td>trace path</td></tr>"
        "<tr><td>selected context files, tool sequence, policy</td><td>candidate patch text</td><td>artifact-relative paths</td></tr>"
        "<tr><td>environment, patch size + SHA-256, feedback</td><td>provider secrets</td><td>schema version</td></tr>"
        "</tbody></table>"
        "<p class='boundary-note'>Exported records are curation inputs for bad-case analysis and regression selection, not automatically production training data.</p></section>",
        f"<details class='provenance'><summary>Feedback provenance</summary><code>{_escape(str(feedback_path or 'not found'))}</code>"
        f"<code>{_escape(str(dataset_path))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_compare_dashboard(project_dir: Path) -> str:
    """Render a dedicated single-agent vs multi-agent evidence view.

    This page answers a narrow engineering question: what changed when the same
    AgentLoop was wrapped in a coordinator with reviewer/verifier roles.
    """

    run_dir = _latest_run_dir(project_dir)
    comparison_path = _latest_comparison_path(project_dir)
    multi_path = _latest_multi_agent_summary_path(project_dir)
    usage_path = _latest_usage_path(project_dir)

    comparison = _read_json_file(comparison_path)
    multi = _read_json_file(multi_path)
    usage = _read_json_file(usage_path)
    summary = usage.get("summary") or {}

    task_id = comparison.get("task_id") or multi.get("task") or "latest local run"
    single_status = str(comparison.get("single_status") or "-")
    multi_status = str(comparison.get("multi_status") or multi.get("status") or "-")
    single_patch = comparison.get("single_patch_generated", "-")
    multi_patch = comparison.get("multi_patch_generated", "-")
    single_cost = comparison.get("single_cost_usd")
    multi_cost = comparison.get("multi_cost_usd")
    cost_delta = None
    if single_cost is not None and multi_cost is not None:
        cost_delta = float(multi_cost) - float(single_cost)
    verifier_status = comparison.get("verifier_status") or "-"
    revision_rounds = comparison.get("revision_rounds", multi.get("revision_rounds", 0))
    recommendation = comparison.get("recommendation") or "Run a compare case to generate a recommendation."
    reviewer_findings = comparison.get("reviewer_findings") or []
    reviewer_text = "<br>".join(_escape(item) for item in reviewer_findings[:3]) or "-"

    body = [
        "<h2>Single vs Multi 对比</h2>",
        "<p class='help strong'>这个面板只回答一个问题：同一个真实缺陷，单 Agent 和多 Agent Coordinator 的工程取舍是什么。</p>",
        _metric_grid(
            [
                ("Case", str(task_id)[:90], "固定 reference case", "neutral"),
                ("单 Agent", single_status, "canonical AgentLoop", _tone_for_status(single_status)),
                ("多 Agent Coordinator", multi_status, "Implementer / Reviewer / Verifier", _tone_for_status(multi_status)),
                ("Patch", f"{single_patch} / {multi_patch}", "single / multi 是否生成 patch", "ok" if single_patch and multi_patch else "warn"),
                ("Verifier", str(verifier_status), "多 Agent 验证角色结论", _tone_for_status(str(verifier_status))),
                ("Cost Delta", "-" if cost_delta is None else f"${cost_delta:.6f}", "multi - single", "warn" if cost_delta and cost_delta > 0 else "ok"),
            ]
        ),
        "<div class='lane-grid'>",
        "<div class='lane-card'>",
        "<h3>单 Agent 路径</h3>",
        "<div class='mini-flow'><span>User task</span><span>AgentLoop</span><span>Tools</span><span>Patch</span></div>",
        "<p class='help'>优点是成本低、路径短、容易理解；风险是缺少独立 review/verifier 控制点。</p>",
        "<table><tbody>",
        f"<tr><td>status</td><td>{_badge(single_status, _tone_for_status(single_status))}</td></tr>",
        f"<tr><td>patch generated</td><td>{_escape(single_patch)}</td></tr>",
        f"<tr><td>LLM calls</td><td>{_escape(comparison.get('single_llm_calls', '-'))}</td></tr>",
        f"<tr><td>tool calls</td><td>{_escape(comparison.get('single_tool_calls', '-'))}</td></tr>",
        f"<tr><td>failed tool calls</td><td>{_escape(comparison.get('single_failed_tool_calls', '-'))}</td></tr>",
        f"<tr><td>cost</td><td>{_format_optional_cost(single_cost)}</td></tr>",
        "</tbody></table>",
        "</div>",
        "<div class='lane-card'>",
        "<h3>多 Agent Coordinator 路径</h3>",
        "<div class='mini-flow'><span>Implementer</span><span>Reviewer</span><span>Verifier</span><span>Artifact</span></div>",
        "<p class='help'>优点是把实现、审查、验证拆成显式控制点；代价是 token、延迟和工具调用更多。</p>",
        "<table><tbody>",
        f"<tr><td>status</td><td>{_badge(multi_status, _tone_for_status(multi_status))}</td></tr>",
        f"<tr><td>patch generated</td><td>{_escape(multi_patch)}</td></tr>",
        f"<tr><td>LLM calls</td><td>{_escape(comparison.get('multi_llm_calls', summary.get('llm_calls', '-')))}</td></tr>",
        f"<tr><td>tool calls</td><td>{_escape(comparison.get('multi_tool_calls', summary.get('tool_calls', '-')))}</td></tr>",
        f"<tr><td>failed tool calls</td><td>{_escape(comparison.get('multi_failed_tool_calls', summary.get('failed_tool_calls', '-')))}</td></tr>",
        f"<tr><td>cost</td><td>{_format_optional_cost(multi_cost)}</td></tr>",
        f"<tr><td>revision rounds</td><td>{_escape(revision_rounds)}</td></tr>",
        "</tbody></table>",
        "</div>",
        "</div>",
        "<h3>Engineering Decision</h3>",
        f"<p class='diagnosis'>{_escape(recommendation)}</p>",
        "<p class='boundary-note'>Multi-agent adds explicit review and verification control points. Whether that trade is useful is decided by matched cost, failure, and evaluation evidence.</p>",
        "<h3>Reviewer / Verifier Decision</h3>",
        "<table><tbody>",
        f"<tr><td>verifier status</td><td>{_escape(verifier_status)}</td></tr>",
        f"<tr><td>reviewer findings</td><td>{reviewer_text}</td></tr>",
        f"<tr><td>recommendation</td><td>{_escape(recommendation)}</td></tr>",
        "</tbody></table>",
        "<h3>Produced Artifacts</h3>",
        _render_artifact_cards(multi),
        "<details class='provenance'><summary>Artifact provenance</summary>"
        f"<code>{_escape(str(run_dir or 'not found'))}</code>"
        f"<code>{_escape(str(comparison_path or 'not found'))}</code>"
        f"<code>{_escape(str(multi_path or 'not found'))}</code>"
        f"<code>{_escape(str(usage_path or 'not found'))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _format_optional_cost(value: Any) -> str:
    """Format an optional cost number for compact comparison tables."""

    if value in (None, ""):
        return "-"
    try:
        return f"${float(value):.6f}"
    except (TypeError, ValueError):
        return _escape(value)


def _render_role_rows(summary: dict[str, Any]) -> str:
    """Render role-level multi-agent status rows."""

    rows = []
    for result in summary.get("role_results") or []:
        final_answer = str(result.get("final_answer") or result.get("output") or "")
        rows.append(
            "<tr>"
            f"<td>{_escape(result.get('role') or result.get('name') or '-')}</td>"
            f"<td>{_badge(str(result.get('decision') or result.get('status') or '-'), _tone_for_status(str(result.get('decision') or result.get('status') or '')))}</td>"
            f"<td>{_escape(result.get('steps', '-'))}</td>"
            f"<td class='mono'>{_escape(result.get('artifact_path') or result.get('artifact') or '-')}</td>"
            f"<td>{_escape(final_answer[:220])}</td>"
            "</tr>"
        )
    if rows:
        return "".join(rows)
    return "<tr><td colspan='5'>No multi-agent role summary found yet. Run multi/compare mode, or use this page as the offline speaking route.</td></tr>"


def _render_artifact_rows(summary: dict[str, Any]) -> str:
    """Render artifact handoff rows from multi-agent summaries."""

    artifacts = summary.get("artifacts") or summary.get("artifact_index") or []
    if isinstance(artifacts, dict):
        artifacts = artifacts.get("artifacts") or list(artifacts.values())
    rows = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        rows.append(
            "<tr>"
            f"<td>{_escape(artifact.get('name') or artifact.get('kind') or artifact.get('artifact_id') or '-')}</td>"
            f"<td>{_escape(artifact.get('producer') or artifact.get('role') or '-')}</td>"
            f"<td class='mono'>{_escape(artifact.get('path') or artifact.get('relative_path') or '-')}</td>"
            "<td>后续角色只读取显式 artifact，避免把中间思考和无关上下文全部塞进 prompt。</td>"
            "</tr>"
        )
    if rows:
        return "".join(rows)
    return (
        "<tr><td>implementer_output</td><td>Implementer</td><td class='mono'>multi_agent/artifacts/*.md</td>"
        "<td>候选 patch / 方案交给 reviewer。</td></tr>"
        "<tr><td>review_findings</td><td>Reviewer</td><td class='mono'>multi_agent/artifacts/*.md</td>"
        "<td>明确 PASS / NEEDS_REVISION / BLOCKED。</td></tr>"
        "<tr><td>verification_result</td><td>Verifier</td><td class='mono'>multi_agent/artifacts/*.md</td>"
        "<td>验证结果触发修订或结束。</td></tr>"
    )


def _metric_grid(items: list[tuple[str, str, str, str]]) -> str:
    """Render reusable metric cards."""

    cards = []
    for label, value, note, tone in items:
        cards.append(
            f"<div class='metric-card {tone}'>"
            f"<div class='metric-label'>{_escape(label)}</div>"
            f"<div class='metric-value'>{_escape(value)}</div>"
            f"<div class='metric-note'>{_escape(note)}</div>"
            "</div>"
        )
    return "<div class='metric-grid'>" + "".join(cards) + "</div>"


def _badge(text: str, tone: str) -> str:
    """Render a colored status badge."""

    return f"<span class='badge {tone}'>{_escape(text)}</span>"


def _tone_for_status(value: str) -> str:
    """Map status text to a display tone."""

    lowered = str(value).lower()
    if "patch_generated" in lowered or lowered in {"ok", "succeeded", "success"}:
        return "ok"
    if any(marker in lowered for marker in ("blocked", "failed", "error", "repeated", "deny")):
        return "bad"
    if any(marker in lowered for marker in ("no_patch", "not_evaluated", "unavailable", "missing")):
        return "warn"
    return "neutral"


def _trace_scope_label(trace_path: Path | None) -> str:
    """Infer whether the current trace came from single, multi, or a smoke run."""

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


def _format_trace_event_label(index: int, event: dict[str, Any]) -> str:
    """Format one trace event without ambiguous separator glyphs.

    The UI is for learning and presentation, so each event shows a stable
    sequence number plus explicit fields such as tool and duration.
    """

    event_type = str(event.get("event_type") or "event")
    names = {
        "task_state_checkpoint": "状态检查点",
        "context_assembly": "上下文组装",
        "plan": "模型计划",
        "planning_mode": "规划模式",
        "llm_call": "模型调用",
        "guardrail_check": "安全检查",
        "clarification_decision": "澄清判断",
        "skill_selection": "Skill 选择",
        "action": "动作解析",
        "file_write": "产物写入",
        "permission_check": "权限检查",
        "hook_check": "工具前置检查",
        "human_approval": "人工审批",
        "human_input_requested": "等待人工输入",
        "human_input_response_loaded": "载入人工回答",
        "human_input_cancelled": "人工输入已取消",
        "tool_call": "工具调用",
        "tool_observation": "工具结果",
        "observation": "观察回填",
        "evidence_collected": "证据记录",
        "recovery_decision": "恢复判断",
        "verifier_result": "验证结论",
        "review_decision": "审查结论",
        "final_answer": "最终回答",
        "stop_hooks": "停止钩子",
        "multi_agent_start": "多 Agent 开始",
        "handoff": "角色交接",
        "agent_stage_start": "角色开始",
        "agent_stage_end": "角色结束",
        "artifact_created": "产物写入",
        "multi_agent_done": "多 Agent 完成",
        "fanout_start": "Fanout 开始",
        "fanout_batch_done": "Fanout 批次完成",
        "fanout_done": "Fanout 完成",
        "finalizer_error": "Finalizer 失败",
    }
    fields = [f"{index}. {names.get(event_type, event_type)}"]
    if event.get("agent"):
        fields.append(f"agent: {event.get('agent')}")
    if event.get("tool_call"):
        fields.append(f"tool: {event.get('tool_call')}")
    if event.get("duration_ms"):
        fields.append(f"time: {int(event.get('duration_ms') or 0)} ms")
    if not bool(event.get("success", True)):
        fields.append("status: failed")
    return " | ".join(fields)


def _event_tone(event_type: str, success: bool) -> str:
    """Map trace event type to a compact visual class."""

    if not success:
        return "bad"
    if event_type in {"llm_call", "plan", "planning_mode"}:
        return "blue"
    if event_type in {
        "context_assembly",
        "observation",
        "handoff",
        "agent_stage_start",
        "agent_stage_end",
        "task_state_checkpoint",
        "evidence_collected",
        "multi_agent_start",
        "multi_agent_done",
    }:
        return "purple"
    if event_type in {"tool_call", "tool_observation", "guardrail_check", "permission_check", "hook_check"}:
        return "ok"
    if event_type in {"action", "human_approval", "clarification_decision", "skill_selection", "recovery_decision"}:
        return "warn"
    return "neutral"


def _empty_evidence(message: str) -> str:
    """Render a friendly empty state."""

    return f"<div class='evidence'><h2>No Evidence Yet</h2><p>{_escape(message)}</p></div>"


def _escape(value: Any) -> str:
    """Escape text before inserting it into UI HTML."""

    return html.escape(str(value), quote=True)


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>NanoHarness Evidence Console</title>
  <link rel="icon" href="data:," />
  <style>
    :root {
      --bg: #f5f5f7;
      --surface: rgba(255, 255, 255, .74);
      --panel: rgba(255, 255, 255, .84);
      --panel-2: rgba(242, 244, 248, .94);
      --panel-3: rgba(250, 250, 252, .92);
      --text: #1d1d1f;
      --muted: #6e7582;
      --line: rgba(60, 60, 67, .16);
      --accent: #0a84ff;
      --accent-strong: #0066cc;
      --blue: #0a84ff;
      --purple: #8e8cf0;
      --yellow: #b7791f;
      --red: #d70015;
      --shadow: rgba(0, 0, 0, .08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
      background: #f4f6f8;
      color: var(--text);
    }
    header {
      padding: 18px 28px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      background: rgba(245, 245, 247, .82);
      backdrop-filter: blur(22px) saturate(1.35);
      position: sticky;
      top: 0;
      z-index: 10;
    }
    h1 { margin: 0; font-size: 25px; font-weight: 760; letter-spacing: 0; }
    .eyebrow {
      color: var(--accent-strong);
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .08em;
      margin-bottom: 6px;
    }
    .subtitle { margin-top: 4px; color: var(--muted); font-size: 14px; }
    .project-chip {
      max-width: 420px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 12px;
      background: rgba(255, 255, 255, .72);
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .header-actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 10px;
      min-width: 360px;
    }
    .header-actions button {
      width: auto;
      margin: 0;
      white-space: nowrap;
    }
    main {
      display: grid;
      grid-template-columns: minmax(360px, 420px) minmax(0, 1fr);
      min-height: calc(100vh - 79px);
    }
    body.sidebar-collapsed main { grid-template-columns: minmax(0, 1fr); }
    body.sidebar-collapsed aside { display: none; }
    body.status-collapsed .status { display: none; }
    body.focus-mode header {
      padding: 10px 18px;
    }
    body.focus-mode .eyebrow,
    body.focus-mode .subtitle,
    body.focus-mode .project-chip,
    body.focus-mode .status,
    body.focus-mode #jobsTitle,
    body.focus-mode #jobs {
      display: none;
    }
    body.focus-mode main {
      min-height: calc(100vh - 49px);
    }
    body.focus-mode section {
      padding: 16px 22px 22px;
    }
    body.focus-mode .output {
      max-height: none;
      min-height: calc(100vh - 134px);
    }
    aside {
      border-right: 1px solid var(--line);
      padding: 20px;
      background: rgba(245, 245, 247, .66);
      backdrop-filter: blur(20px);
      overflow-y: auto;
    }
    section {
      padding: 18px 28px 28px;
      max-width: 1720px;
      width: 100%;
      margin: 0 auto;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 14px;
      box-shadow: 0 12px 34px var(--shadow);
    }
    .card h2 {
      font-size: 16px;
      margin: 0 0 10px;
    }
    .section-kicker {
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: .06em;
      text-transform: uppercase;
      margin-bottom: 6px;
    }
    .route-map {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
      margin: 10px 0 4px;
    }
    .route-step {
      display: grid;
      grid-template-columns: 34px 1fr;
      gap: 10px;
      align-items: start;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: var(--panel-3);
    }
    .route-num {
      width: 26px;
      height: 26px;
      border-radius: 999px;
      display: grid;
      place-items: center;
      font-weight: 900;
      color: white;
      background: var(--accent);
    }
    .route-title { font-weight: 800; }
    .route-copy { color: var(--muted); font-size: 12px; line-height: 1.5; margin-top: 2px; }
    .help {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
      margin: 6px 0 10px;
    }
    .command {
      display: block;
      margin-top: 6px;
      color: #42556b;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 11px;
      overflow-wrap: anywhere;
    }
    label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin: 10px 0 6px;
    }
    input, select, textarea {
      width: 100%;
      background: rgba(255, 255, 255, .9);
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
    }
    textarea { min-height: 76px; resize: vertical; }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .form-row {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 10px;
    }
    .wide { grid-column: 1 / -1; }
    .checkbox-line {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 10px;
      color: var(--muted);
      font-size: 12px;
    }
    .checkbox-line input { width: auto; }
    .quick-tasks {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 10px;
    }
    .quick-tasks button {
      width: auto;
      margin: 0;
      padding: 8px;
      font-size: 12px;
    }
    button {
      width: 100%;
      border: 0;
      border-radius: 6px;
      background: var(--blue);
      color: white;
      font-weight: 700;
      padding: 9px 12px;
      margin-top: 10px;
      cursor: pointer;
      transition: transform .12s ease, box-shadow .12s ease, background .12s ease, filter .12s ease;
    }
    button:hover { filter: brightness(1.03); transform: translateY(-1px); }
    button:active { transform: translateY(0); filter: brightness(.98); }
    button.secondary { background: var(--panel-2); color: var(--text); border: 1px solid var(--line); }
    button.warn { background: #fff4d8; color: #7a4d00; }
    button.primary { background: var(--accent); color: white; }
    button.ghost { background: transparent; color: var(--text); border: 1px solid var(--line); }
    .action-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 10px;
    }
    .action-grid button { margin-top: 0; }
    details {
      border-top: 1px solid var(--line);
      margin-top: 12px;
      padding-top: 10px;
    }
    summary {
      color: var(--muted);
      cursor: pointer;
      font-size: 12px;
    }
    .status {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .pill {
      padding: 12px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: var(--panel);
    }
    .pill .k { color: var(--muted); font-size: 12px; }
    .pill .v { margin-top: 4px; font-size: 14px; overflow-wrap: anywhere; }
    .tabs { display: flex; gap: 8px; margin-bottom: 12px; }
    .tabs button { width: auto; margin: 0; padding: 8px 10px; }
    .view-tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
      align-items: center;
      position: sticky;
      top: 81px;
      z-index: 8;
      padding: 10px 0;
      background: #f4f6f8;
    }
    .view-tabs button {
      width: auto;
      margin: 0;
      padding: 8px 12px;
      font-size: 13px;
      color: var(--text);
      background: rgba(255, 255, 255, .78);
      border: 1px solid var(--line);
      box-shadow: 0 2px 8px rgba(0, 0, 0, .04);
    }
    .view-tabs button.active {
      color: #0057b8;
      background: #e8f3ff;
      border-color: rgba(10, 132, 255, .34);
      box-shadow: 0 3px 10px rgba(10, 132, 255, .12);
    }
    .output {
      white-space: pre-wrap;
      word-break: break-word;
      background: rgba(255, 255, 255, .88);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 24px 28px;
      min-height: calc(100vh - 245px);
      max-height: none;
      overflow: auto;
      color: var(--text);
      box-shadow: 0 18px 46px rgba(0, 0, 0, .07);
    }
    .evidence { white-space: normal; color: var(--text); }
    .evidence h2 { margin: 0 0 8px; font-size: 20px; }
    .evidence h3 { margin: 18px 0 8px; font-size: 15px; }
    .strong { color: #2f3338; }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin: 12px 0;
    }
    .metric-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: rgba(250, 250, 252, .88);
    }
    .metric-card.ok { border-color: rgba(52, 199, 89, .34); background: rgba(245, 252, 247, .92); }
    .metric-card.warn { border-color: rgba(183, 121, 31, .3); background: rgba(255, 250, 240, .92); }
    .metric-card.bad { border-color: rgba(215, 0, 21, .28); background: rgba(255, 247, 248, .92); }
    .metric-label, .label { color: var(--muted); font-size: 12px; margin-right: 8px; }
    .metric-value { margin-top: 4px; font-size: 18px; font-weight: 800; overflow-wrap: anywhere; }
    .metric-note { margin-top: 4px; color: var(--muted); font-size: 12px; }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      overflow-wrap: anywhere;
    }
    .badge {
      display: inline-block;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 700;
      border: 1px solid var(--line);
      color: var(--text);
      background: rgba(242, 244, 248, .84);
    }
    .badge.ok, .event-pill.ok { border-color: rgba(52, 199, 89, .34); color: #248a3d; background: rgba(52, 199, 89, .09); }
    .badge.warn, .event-pill.warn { border-color: rgba(255, 149, 0, .34); color: #a35f00; background: rgba(255, 149, 0, .1); }
    .badge.bad, .event-pill.bad { border-color: rgba(215, 0, 21, .28); color: var(--red); background: rgba(215, 0, 21, .08); }
    .event-pill.blue { border-color: rgba(10, 132, 255, .32); color: #0057b8; background: rgba(10, 132, 255, .09); }
    .event-pill.purple { border-color: rgba(142, 140, 240, .34); color: #5e5ce6; background: rgba(142, 140, 240, .1); }
    .event-pill.neutral { color: var(--muted); }
    table {
      width: 100%;
      border-collapse: collapse;
      margin: 8px 0 12px;
      font-size: 13px;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 9px 8px;
      text-align: left;
      vertical-align: top;
    }
    tr:hover td { background: rgba(0, 0, 0, .018); }
    th { color: var(--muted); font-weight: 700; white-space: nowrap; word-break: normal; }
    td { overflow-wrap: anywhere; }
    .split {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }
    .talking-list {
      margin: 8px 0 12px;
      padding-left: 22px;
      color: #2f3338;
      line-height: 1.65;
    }
    .talking-list li { margin: 5px 0; }
    .flow-strip {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 8px;
      margin: 10px 0 12px;
    }
    .flow-strip span {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: rgba(255, 255, 255, .72);
      padding: 10px 8px;
      text-align: center;
      color: #405266;
      font-size: 12px;
      font-weight: 700;
    }
    .lane-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin: 12px 0;
    }
    .lane-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      background: rgba(250, 250, 252, .82);
    }
    .mini-flow {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 6px;
      margin: 10px 0;
    }
    .mini-flow span {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 6px;
      text-align: center;
      color: #2f3338;
      font-size: 12px;
      background: rgba(255, 255, 255, .72);
    }
    .timeline-step {
      border-left: 2px solid rgba(10, 132, 255, .18);
      padding: 12px 0 12px 16px;
      margin-left: 6px;
    }
    .timeline-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
    }
    .event-pill {
      display: inline-block;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      margin: 0 7px 7px 0;
      font-size: 12px;
      background: rgba(255, 255, 255, .76);
    }
    .legend-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 12px 0;
    }
    .legend-item {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      background: rgba(255, 255, 255, .76);
      color: var(--muted);
    }
    .legend-item.blue { border-color: rgba(10, 132, 255, .32); color: #0057b8; background: rgba(10, 132, 255, .09); }
    .legend-item.purple { border-color: rgba(142, 140, 240, .34); color: #5e5ce6; background: rgba(142, 140, 240, .1); }
    .legend-item.ok { border-color: rgba(52, 199, 89, .34); color: #248a3d; background: rgba(52, 199, 89, .09); }
    .legend-item.bad { border-color: rgba(215, 0, 21, .28); color: var(--red); background: rgba(215, 0, 21, .08); }
    .legend-item.neutral {
      color: var(--muted);
    }
    .raw-text {
      white-space: pre-wrap;
      color: #2f3b49;
      margin: 0;
      font: inherit;
    }
    .job {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 10px;
      margin-bottom: 10px;
      cursor: pointer;
    }
    .job strong { display: block; }
    .job span { color: var(--muted); font-size: 12px; }
    .succeeded { color: var(--accent-strong); }
    .failed { color: var(--red); }
    .running { color: var(--yellow); }
    @media (max-width: 900px) {
      header {
        position: static;
        align-items: stretch;
        flex-direction: column;
        padding: 14px 16px;
        gap: 12px;
      }
      h1 { font-size: 24px; }
      .header-actions {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        min-width: 0;
        gap: 8px;
      }
      .header-actions button {
        width: 100%;
        min-width: 0;
        white-space: normal;
        padding: 8px 6px;
      }
      .project-chip {
        grid-column: 1 / -1;
        max-width: none;
        padding: 8px 10px;
      }
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      section { padding: 10px 12px 18px; }
      .view-tabs {
        top: 0;
        flex-wrap: nowrap;
        overflow-x: auto;
        overscroll-behavior-x: contain;
        padding: 8px 0;
        scrollbar-width: thin;
      }
      .view-tabs button { flex: 0 0 auto; }
      .output { padding: 18px 16px; min-height: calc(100vh - 250px); }
      .status { grid-template-columns: 1fr; }
      .metric-grid, .split, .form-grid, .form-row, .quick-tasks, .flow-strip, .lane-grid, .mini-flow, .action-grid { grid-template-columns: 1fr; }
    }

    /* Evidence console v2: dense operational surface, not a marketing page. */
    :root {
      --bg: #eef1f4;
      --surface: #ffffff;
      --panel: #ffffff;
      --panel-2: #f6f7f9;
      --panel-3: #fafbfc;
      --text: #17191d;
      --muted: #66707d;
      --line: #d9dee5;
      --accent: #1769e0;
      --accent-strong: #1157bb;
      --blue: #1769e0;
      --purple: #6d4bd1;
      --yellow: #9a6200;
      --red: #b4232f;
      --green: #16794a;
    }
    body { background: var(--bg); }
    header {
      min-height: 64px;
      padding: 0 20px;
      background: #15181d;
      color: #fff;
      border: 0;
      backdrop-filter: none;
      box-shadow: none;
    }
    .brand-lockup { display: flex; align-items: center; gap: 12px; min-width: 0; }
    .brand-mark {
      display: grid; place-items: center; width: 34px; height: 34px;
      border: 1px solid #4d5662; border-radius: 6px; color: #9ec2ff;
      font-weight: 800; font-size: 13px;
    }
    header h1 { font-size: 17px; font-weight: 700; }
    header .subtitle { color: #9fa8b4; font-size: 12px; margin: 2px 0 0; }
    header .eyebrow { display: none; }
    .header-actions { min-width: 0; gap: 6px; }
    .header-actions button {
      width: auto; padding: 7px 10px; background: transparent; color: #dbe2eb;
      border-color: #424a55; font-size: 12px; box-shadow: none;
    }
    .header-actions button:hover { background: #252a31; }
    .project-chip {
      max-width: 290px; padding: 7px 9px; border-color: #424a55;
      background: #20242a; color: #aeb8c4; white-space: nowrap;
      overflow: hidden; text-overflow: ellipsis;
    }
    main { grid-template-columns: 340px minmax(0, 1fr); max-width: none; min-height: calc(100vh - 64px); }
    aside {
      width: 340px; padding: 14px; background: #f7f8fa;
      border-right: 1px solid var(--line); overflow-y: auto; max-height: calc(100vh - 64px);
    }
    body.sidebar-collapsed main { grid-template-columns: minmax(0, 1fr); }
    body.sidebar-collapsed aside { display: none; }
    section { padding: 0 24px 32px; min-width: 0; }
    aside .card { padding: 14px 0 18px; margin: 0; border: 0; border-bottom: 1px solid var(--line); background: transparent; box-shadow: none; }
    aside .card:last-child { border-bottom: 0; }
    aside h2 { font-size: 14px; margin: 0 0 12px; }
    .section-kicker, .view-kicker {
      display: block; color: #687587; font-size: 10px; font-weight: 800;
      letter-spacing: 0; text-transform: uppercase; margin-bottom: 4px;
    }
    label { margin: 10px 0 5px; color: #4d5867; font-size: 11px; font-weight: 700; }
    input, select, textarea {
      min-height: 34px; padding: 7px 9px; border: 1px solid #cfd5dd;
      border-radius: 5px; background: #fff; font-size: 12px; color: var(--text);
      box-shadow: none;
    }
    textarea { min-height: 92px; resize: vertical; }
    button { border-radius: 5px; box-shadow: none; font-size: 12px; min-height: 34px; }
    button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    button.secondary { background: #fff; color: #303845; border-color: #cfd5dd; }
    .checkbox-line { padding: 8px 0; gap: 7px; font-size: 11px; color: #536070; }
    .checkbox-line input { min-height: 0; }
    details { border-radius: 5px; }
    details summary { cursor: pointer; font-size: 12px; font-weight: 700; }
    .status {
      position: sticky; top: 64px; z-index: 8; display: grid;
      grid-template-columns: 110px minmax(220px, 1fr) 160px minmax(160px, .7fr);
      gap: 0; margin: 0 -24px; padding: 0 24px; background: #fff;
      border-bottom: 1px solid var(--line);
    }
    .status .pill { border: 0; border-right: 1px solid var(--line); border-radius: 0; padding: 10px 14px; background: transparent; }
    .status .pill:first-child { border-left: 1px solid var(--line); }
    .pill .k { color: #768191; font-size: 9px; text-transform: uppercase; font-weight: 800; }
    .pill .v { margin-top: 2px; font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .view-tabs {
      position: sticky; top: 110px; z-index: 7; display: flex; flex-wrap: nowrap;
      gap: 0; margin: 0 -24px 20px; padding: 0 24px; overflow-x: auto;
      background: #fff; border-bottom: 1px solid var(--line); backdrop-filter: none;
    }
    body.status-collapsed .view-tabs { top: 64px; }
    .view-tabs button {
      flex: 0 0 auto; width: auto; min-height: 42px; margin: 0; padding: 0 13px;
      color: #5a6573; background: transparent; border: 0; border-bottom: 2px solid transparent;
      border-radius: 0; font-weight: 650;
    }
    .view-tabs button.active { color: var(--accent-strong); background: transparent; border-bottom-color: var(--accent); }
    .view-tabs .utility { margin-left: auto; color: #6c7785; }
    .output { min-height: 620px; padding: 0; background: transparent; border: 0; border-radius: 0; box-shadow: none; }
    .evidence { max-width: 1320px; margin: 0 auto; }
    .evidence h2 { margin: 0; font-size: 22px; }
    .evidence h3 { margin: 0; font-size: 15px; }
    .evidence h4 { margin: 3px 0 0; font-size: 14px; }
    .view-heading, .section-title {
      display: flex; align-items: flex-start; justify-content: space-between; gap: 16px;
    }
    .view-heading { padding: 4px 0 18px; border-bottom: 1px solid #cfd5dd; }
    .section-title { align-items: center; margin-bottom: 14px; }
    .section-title > span, .claim-note { color: var(--muted); font-size: 11px; }
    .evidence-section { padding: 22px 0; border-bottom: 1px solid var(--line); }
    .metric-grid { grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 1px; margin: 0; background: var(--line); border-bottom: 1px solid var(--line); }
    .metric { min-height: 96px; padding: 15px; border: 0; border-radius: 0; background: #fff; }
    .metric .metric-value { font-size: 17px; overflow-wrap: anywhere; }
    .metric .metric-label { font-size: 10px; text-transform: uppercase; }
    .metric .metric-help { font-size: 10px; }
    table { display: table; width: 100%; table-layout: fixed; border-collapse: collapse; background: #fff; border: 1px solid var(--line); }
    th, td { padding: 10px 12px; border-bottom: 1px solid var(--line); overflow-wrap: anywhere; vertical-align: top; font-size: 12px; }
    th { background: #f4f6f8; color: #647081; font-size: 10px; text-transform: uppercase; }
    .pipeline { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); border: 1px solid var(--line); background: #fff; }
    .pipeline > div { min-height: 92px; padding: 13px; border-right: 1px solid var(--line); }
    .pipeline > div:last-child { border-right: 0; }
    .pipeline b { display: block; color: #9aa3af; font-size: 10px; }
    .pipeline span { display: block; margin: 10px 0 4px; font-weight: 750; font-size: 13px; }
    .pipeline small { color: var(--muted); font-size: 10px; }
    .claim-ladder { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
    .claim-step { min-height: 96px; padding: 14px; border: 1px solid var(--line); border-top: 3px solid #8b95a3; background: #fff; border-radius: 5px; }
    .claim-step.ok { border-top-color: var(--green); }
    .claim-step.warn { border-top-color: #c17b00; }
    .claim-step.bad { border-top-color: var(--red); }
    .claim-step span, .claim-step small { display: block; color: var(--muted); font-size: 10px; }
    .claim-step strong { display: block; margin: 10px 0 4px; font-size: 14px; overflow-wrap: anywhere; }
    .artifact-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .artifact-card { padding: 15px; border: 1px solid var(--line); border-radius: 6px; background: #fff; }
    .artifact-head { display: flex; justify-content: space-between; gap: 12px; }
    .artifact-head span { color: var(--purple); font-size: 10px; font-weight: 800; text-transform: uppercase; }
    .artifact-card p { min-height: 74px; color: #3e4753; font-size: 12px; line-height: 1.55; }
    .artifact-handoff { display: flex; gap: 18px; padding-top: 10px; border-top: 1px solid var(--line); color: var(--muted); font-size: 10px; }
    .artifact-card details { margin-top: 10px; padding: 0; border: 0; }
    .artifact-card code, .provenance code { display: block; margin-top: 7px; color: #66707d; overflow-wrap: anywhere; white-space: normal; font-size: 10px; }
    .provenance { margin-top: 16px; padding: 11px 13px; border: 1px solid var(--line); background: #f7f8fa; }
    .capability-strip { display: grid; grid-template-columns: repeat(4, 1fr); border: 1px solid var(--line); background: #fff; }
    .capability-strip div { padding: 16px; border-right: 1px solid var(--line); }
    .capability-strip div:last-child { border: 0; }
    .capability-strip b, .capability-strip span { display: block; }
    .capability-strip b { font-size: 21px; }
    .capability-strip span { margin-top: 4px; color: var(--muted); font-size: 10px; }
    .coordination-graph { display: grid; grid-template-columns: 1fr auto 1fr auto 1fr auto 1fr; align-items: center; gap: 8px; }
    .coordination-graph > div { min-height: 78px; padding: 14px; border: 1px solid var(--line); background: #fff; border-radius: 5px; }
    .coordination-graph b, .coordination-graph span { display: block; }
    .coordination-graph span { margin-top: 5px; color: var(--muted); font-size: 10px; }
    .coordination-graph i { color: #8b95a3; font-size: 9px; font-style: normal; text-transform: uppercase; }
    .diagnosis { padding: 14px; border-left: 3px solid var(--accent); background: #f4f7fc; font-size: 13px; }
    .boundary-note { margin: 12px 0 0; color: #596575; font-size: 11px; }
    .evidence-list { display: flex; flex-wrap: wrap; gap: 6px; }
    .evidence-list span { padding: 5px 8px; border: 1px solid var(--line); border-radius: 4px; background: #fff; color: #526071; font-size: 10px; }
    .next-actions { margin: 0; padding-left: 20px; }
    .next-actions li { padding: 4px 0; font-size: 12px; }
    .timeline-lane { margin-bottom: 8px; }
    .run-facts { display: flex; flex-wrap: wrap; gap: 14px; margin: 10px 0 14px; color: var(--muted); font-size: 10px; }
    .event-pill, .legend-item { border-radius: 4px; }
    #jobsTitle, #jobs { max-width: 1320px; margin-left: auto; margin-right: auto; }
    .empty-inline { padding: 18px; border: 1px dashed #c7cdd5; color: var(--muted); font-size: 12px; }
    @media (max-width: 1100px) {
      .metric-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .pipeline { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .pipeline > div:nth-child(3) { border-right: 0; }
      .pipeline > div:nth-child(-n+3) { border-bottom: 1px solid var(--line); }
    }
    @media (max-width: 900px) {
      header { position: sticky; padding: 10px 12px; flex-direction: row; align-items: center; gap: 8px; }
      header .subtitle, .project-chip, #statusToggle, #focusToggle { display: none; }
      .header-actions { display: flex; }
      main, body.sidebar-collapsed main { grid-template-columns: 1fr; }
      aside { position: fixed; inset: 58px 0 0 0; z-index: 20; width: min(340px, 92vw); max-height: none; box-shadow: 12px 0 32px rgba(0,0,0,.15); }
      body.sidebar-collapsed aside { display: none; }
      section { padding: 0 12px 24px; }
      .status { top: 58px; margin: 0 -12px; padding: 0 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .status .pill:nth-child(n+3) { display: none; }
      .view-tabs { top: 105px; margin: 0 -12px 16px; padding: 0 12px; }
      .view-tabs .utility { margin-left: 0; }
      .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .metric { min-height: 88px; }
      .pipeline, .claim-ladder, .artifact-grid, .capability-strip { grid-template-columns: 1fr; }
      .pipeline > div, .capability-strip div { border-right: 0; border-bottom: 1px solid var(--line); }
      .coordination-graph { grid-template-columns: 1fr; }
      .coordination-graph i { text-align: center; }
      table { display: block; overflow-x: auto; table-layout: auto; }
      table thead, table tbody { display: table; min-width: 680px; width: 100%; table-layout: auto; }
      th, td { overflow-wrap: normal; word-break: normal; }
      .artifact-card p { min-height: 0; }
      .view-heading { align-items: center; }
    }
  </style>
</head>
<body class="sidebar-collapsed status-collapsed">
  <header>
    <div class="brand-lockup">
      <div class="brand-mark">NH</div>
      <div>
        <h1>NanoHarness Evidence Console</h1>
        <div class="subtitle">Runtime control, orchestration, evaluation and improvement evidence</div>
      </div>
    </div>
    <div class="header-actions">
      <button id="sidebarToggle" onclick="toggleSidebar()" title="Toggle run controls">Run controls</button>
      <button id="statusToggle" onclick="toggleStatusBar()" title="Toggle run status">Status</button>
      <button id="focusToggle" onclick="toggleFocusMode()" title="Focus evidence surface">Focus</button>
      <div class="project-chip" id="projectDir"></div>
    </div>
  </header>
  <main>
    <aside>
      <div class="card">
        <div class="section-kicker">BENCHMARK RUN</div>
        <h2>Reference Case</h2>
        <div class="form-grid">
          <div>
            <label>Provider</label>
            <select id="provider">
              <option value="deepseek">DeepSeek</option>
              <option value="openai">OpenAI</option>
              <option value="openai-compatible">OpenAI-compatible</option>
            </select>
          </div>
          <div>
            <label>Model</label>
            <input id="model" value="deepseek-v4-flash" />
          </div>
          <div class="wide">
            <label>Base URL</label>
            <input id="baseUrl" value="https://api.deepseek.com" />
          </div>
          <div class="wide">
            <label>API Key</label>
            <input id="apiKey" type="password" placeholder="留空时使用本机环境变量；页面输入只用于本次运行" />
          </div>
        </div>
        <div class="form-row">
          <div>
            <label>Max Steps</label>
            <input id="maxSteps" type="number" min="1" max="80" value="40" />
          </div>
          <div>
            <label>Context Chars</label>
            <input id="maxContextChars" type="number" min="1000" max="120000" value="18000" />
          </div>
          <div>
            <label>Approval</label>
            <select id="approvalMode">
              <option value="trusted">trusted</option>
              <option value="on-risk">on-risk</option>
              <option value="on-write">on-write</option>
              <option value="dry-run">dry-run</option>
              <option value="locked">locked</option>
            </select>
          </div>
        </div>
        <div class="form-row">
          <div>
            <label>Isolation</label>
            <select id="executionMode">
              <option value="worktree">worktree</option>
              <option value="local">local</option>
              <option value="container">container</option>
            </select>
          </div>
          <div>
            <label>Network</label>
            <select id="networkPolicy"><option value="deny">deny</option><option value="allow">allow</option></select>
          </div>
          <div>
            <label>Tool Routing</label>
            <select id="toolRouting"><option value="task-aware">task-aware</option><option value="all">all (ablation)</option></select>
          </div>
        </div>
        <div class="checkbox-line"><input id="autoApproveWrites" type="checkbox" checked /><span>Auto-approve writes</span></div>
        <div class="checkbox-line"><input id="keepWorktree" type="checkbox" /><span>Keep execution snapshot</span></div>
        <label>Agent Mode</label>
        <select id="benchAgentMode">
          <option value="compare">single + sequential multi</option>
          <option value="multi">sequential multi only</option>
          <option value="single">single AgentLoop only</option>
        </select>
        <div class="checkbox-line">
          <input id="directBaseline" type="checkbox" checked />
          <span>Direct model baseline</span>
        </div>
        <div class="checkbox-line">
          <input id="officialEvaluate" type="checkbox" />
          <span>Official SWE-bench evaluation</span>
        </div>
        <label>Official Eval Workers</label>
        <input id="maxWorkers" type="number" min="1" max="8" value="1" />
        <button class="primary" onclick="startJob('swebench_sample')">Run Reference Case</button>
        <details>
          <summary>Core regression set</summary>
          <button class="secondary" onclick="startJob('swebench_regression')">Run 5 Cases</button>
        </details>
      </div>
      <div class="card">
        <div class="section-kicker">REPOSITORY RUN</div>
        <h2>Agent Task</h2>
        <label>Task</label>
        <textarea id="task">检查当前仓库的 AgentLoop 调用链，给出一个小而安全的代码改进，并保留 trace 和 usage 证据。</textarea>
        <div class="quick-tasks">
          <button class="secondary" onclick="setTaskPreset('repo')">读懂仓库</button>
          <button class="secondary" onclick="setTaskPreset('fix')">修复问题</button>
          <button class="secondary" onclick="setTaskPreset('refactor')">安全重构</button>
          <button class="secondary" onclick="setTaskPreset('doc')">补充说明</button>
        </div>
        <label>Run Mode</label>
        <select id="runAgentMode">
          <option value="single">single</option>
          <option value="multi">sequential multi-role</option>
          <option value="fanout">live fanout</option>
        </select>
        <label>Fanout Plan</label>
        <input id="fanoutPlan" value="examples/fanout-plan.sample.json" />
        <label>Fanout Resume</label>
        <input id="fanoutResume" placeholder=".agent_forge/runs/run-id" />
        <label>Fanout Workers</label>
        <input id="fanoutMaxWorkers" type="number" min="1" max="8" value="4" />
        <details>
          <summary>Workspace, Skills, MCP and output</summary>
          <label>Workspace</label>
          <input id="workspace" value="." />
          <label>Skills</label>
          <input id="skills" value="auto" />
          <label>Skill Manifests</label>
          <input id="skillManifests" placeholder="多个文件用逗号分隔" />
          <label>MCP Config</label>
          <input id="mcpConfig" value="mcp_tools.json" />
          <label>MCP Tools</label>
          <input id="mcpTools" placeholder="forge.web_search, forge.web_fetch" />
          <label>Output Root</label>
          <input id="outputRoot" value=".agent_forge/runs" />
        </details>
        <button class="primary" onclick="startJob('agent_run')">Run Agent Task</button>
      </div>
      <div class="card">
        <div class="section-kicker">IMPROVEMENT LOOP</div>
        <h2>Human Feedback</h2>
        <label>Outcome</label>
        <select id="feedbackOutcome">
          <option value="needs_work">needs_work</option>
          <option value="accepted">accepted</option>
          <option value="rejected">rejected</option>
        </select>
        <label>Labels</label>
        <input id="feedbackLabels" placeholder="tool-routing, validation-gap" />
        <label>Note</label>
        <textarea id="feedbackNote" placeholder="Evidence-grounded judgment"></textarea>
        <button class="secondary" onclick="startJob('feedback')">Record Feedback</button>
        <div class="checkbox-line"><input id="requireFeedback" type="checkbox" checked /><span>Export reviewed runs only</span></div>
        <button class="secondary" onclick="startJob('export_dataset')">Export Evidence Dataset</button>
      </div>
      <div class="card">
        <div class="section-kicker">SYSTEM</div>
        <h2>Environment</h2>
        <button class="secondary" onclick="startJob('doctor')">Doctor</button>
        <button class="secondary" onclick="startJob('verify')">Verify</button>
        <details>
          <summary>Raw report</summary>
          <button class="secondary" onclick="loadEvidence('raw_report')">Open report.md</button>
        </details>
      </div>
    </aside>
    <section>
      <div class="status">
        <div class="pill"><div class="k">Runtime</div><div class="v" id="python"></div></div>
        <div class="pill"><div class="k">Latest Run</div><div class="v" id="latestRun"></div></div>
        <div class="pill"><div class="k">Current View</div><div class="v" id="currentView">Overview</div></div>
        <div class="pill"><div class="k">Active Job</div><div class="v" id="activeJob">none</div></div>
      </div>
      <div class="view-tabs">
        <button data-view="evidence" onclick="loadEvidence('evidence')">Overview</button>
        <button data-view="controls" onclick="loadEvidence('controls')">Runtime Controls</button>
        <button data-view="orchestration" onclick="loadEvidence('orchestration')">Orchestration</button>
        <button data-view="evaluation" onclick="loadEvidence('evaluation')">Evaluation</button>
        <button data-view="compare" onclick="loadEvidence('compare')">Single vs Multi</button>
        <button data-view="usage" onclick="loadEvidence('usage')">Efficiency</button>
        <button data-view="timeline" onclick="loadEvidence('timeline')">Timeline</button>
        <button data-view="feedback" onclick="loadEvidence('feedback')">Feedback Loop</button>
        <button class="utility" onclick="refreshStatus()" title="Refresh status">Refresh</button>
      </div>
      <div id="output" class="output">Loading runtime evidence...</div>
      <h2 id="jobsTitle" style="font-size:14px">Recent Jobs</h2>
      <div id="jobs"></div>
    </section>
  </main>
  <script>
    let currentJob = null;
    const evidenceTitles = {
      evidence: 'Overview',
      controls: 'Runtime Controls',
      orchestration: 'Orchestration',
      evaluation: 'Evaluation',
      compare: 'Single vs Multi',
      summary: 'Result Summary',
      usage: 'Efficiency',
      timeline: 'Execution Timeline',
      feedback: 'Feedback Loop',
      raw_report: 'Raw Report'
    };
    const taskPresets = {
      repo: '阅读当前仓库结构，说明 AgentLoop、Context、ToolRouter、Skill、MCP、Trace 的主调用链，不要修改文件。',
      fix: '定位当前仓库里一个真实的小问题，先解释根因，再做最小代码修改，并运行必要验证。',
      refactor: '找出一个影响可读性的局部实现，做安全重构，不改变业务行为，并说明为什么这样更容易维护。',
      doc: '检查当前仓库的用户入口和运行证据说明，补充缺失的文档内容，避免重复和空泛。'
    };
    const providerDefaults = {
      deepseek: {model: 'deepseek-v4-flash', baseUrl: 'https://api.deepseek.com'},
      openai: {model: 'gpt-4.1-mini', baseUrl: 'https://api.openai.com/v1'},
      'openai-compatible': {model: '', baseUrl: ''}
    };

    async function refreshStatus() {
      const res = await fetch('/api/status');
      const data = await res.json();
      document.getElementById('projectDir').textContent = data.project_dir;
      document.getElementById('python').textContent = data.python;
      document.getElementById('latestRun').textContent = data.latest_run || 'none';
      const jobs = document.getElementById('jobs');
      jobs.innerHTML = '';
      for (const job of data.jobs) {
        const item = document.createElement('div');
        item.className = 'job';
        item.onclick = () => pollJob(job.id, false);
        item.innerHTML = `<strong>${job.title}</strong><span class="${job.status}">${job.status} · ${job.id}</span>`;
        jobs.appendChild(item);
      }
    }

    async function startJob(action) {
      const payload = {
        action,
        task: valueOf('task'),
        provider: valueOf('provider'),
        model: valueOf('model'),
        baseUrl: valueOf('baseUrl'),
        apiKey: valueOf('apiKey'),
        workspace: valueOf('workspace'),
        maxSteps: valueOf('maxSteps'),
        maxContextChars: valueOf('maxContextChars'),
        approvalMode: valueOf('approvalMode'),
        executionMode: valueOf('executionMode'),
        networkPolicy: valueOf('networkPolicy'),
        toolRouting: valueOf('toolRouting'),
        autoApproveWrites: checked('autoApproveWrites'),
        keepWorktree: checked('keepWorktree'),
        outputRoot: valueOf('outputRoot'),
        skills: valueOf('skills'),
        skillManifests: valueOf('skillManifests'),
        mcpConfig: valueOf('mcpConfig'),
        mcpTools: valueOf('mcpTools'),
        runAgentMode: valueOf('runAgentMode'),
        fanoutPlan: valueOf('fanoutPlan'),
        fanoutResume: valueOf('fanoutResume'),
        fanoutMaxWorkers: valueOf('fanoutMaxWorkers'),
        benchAgentMode: valueOf('benchAgentMode'),
        directBaseline: checked('directBaseline'),
        officialEvaluate: checked('officialEvaluate'),
        maxWorkers: valueOf('maxWorkers'),
        feedbackOutcome: valueOf('feedbackOutcome'),
        feedbackLabels: valueOf('feedbackLabels'),
        feedbackNote: valueOf('feedbackNote'),
        requireFeedback: checked('requireFeedback'),
        limit: 1
      };
      const res = await fetch('/api/jobs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const job = await res.json();
      if (job.error) {
        document.getElementById('output').textContent = job.error;
        return;
      }
      currentJob = job.id;
      document.getElementById('activeJob').textContent = `${job.title} · ${job.id}`;
      pollJob(job.id, true);
      refreshStatus();
    }

    async function pollJob(id, keepPolling) {
      const res = await fetch(`/api/jobs/${id}`);
      const job = await res.json();
      const text = [
        `${job.title}`,
        `status=${job.status} exit=${job.exit_code ?? ''}`,
        '',
        job.output || '(running...)'
      ].join('\n');
      document.getElementById('output').textContent = text;
      if (keepPolling && job.status === 'running') {
        setTimeout(() => pollJob(id, true), 1200);
      } else {
        refreshStatus();
      }
    }

    async function loadLatestReport() {
      const res = await fetch('/api/latest-report');
      const data = await res.json();
      document.getElementById('output').textContent = data.content;
      refreshStatus();
    }

    async function loadEvidence(kind) {
      const res = await fetch(`/api/evidence?kind=${encodeURIComponent(kind)}`);
      const data = await res.json();
      document.getElementById('output').innerHTML = data.html;
      setActiveEvidence(kind);
      refreshStatus();
    }

    function setActiveEvidence(kind) {
      const title = evidenceTitles[kind] || kind;
      document.getElementById('currentView').textContent = title;
      for (const button of document.querySelectorAll('[data-view]')) {
        button.classList.toggle('active', button.dataset.view === kind);
      }
    }

    function clearOutput() {
      document.getElementById('output').textContent = '';
      document.getElementById('activeJob').textContent = 'none';
    }

    function toggleSidebar() {
      const collapsed = document.body.classList.toggle('sidebar-collapsed');
      updateLayoutControls();
      return collapsed;
    }

    function toggleFocusMode() {
      const enabled = document.body.classList.toggle('focus-mode');
      if (enabled) {
        document.body.classList.add('sidebar-collapsed');
        document.body.classList.add('status-collapsed');
      }
      updateLayoutControls();
      return enabled;
    }

    function toggleStatusBar() {
      const collapsed = document.body.classList.toggle('status-collapsed');
      updateLayoutControls();
      return collapsed;
    }

    function updateLayoutControls() {
      const sidebarHidden = document.body.classList.contains('sidebar-collapsed');
      const statusHidden = document.body.classList.contains('status-collapsed');
      const focused = document.body.classList.contains('focus-mode');
      const sidebarToggle = document.getElementById('sidebarToggle');
      const statusToggle = document.getElementById('statusToggle');
      const focusToggle = document.getElementById('focusToggle');
      if (sidebarToggle) {
        sidebarToggle.textContent = sidebarHidden ? 'Run controls' : 'Close controls';
      }
      if (statusToggle) {
        statusToggle.textContent = statusHidden ? 'Show status' : 'Hide status';
      }
      if (focusToggle) {
        focusToggle.textContent = focused ? 'Exit focus' : 'Focus';
      }
    }

    function setTaskPreset(name) {
      document.getElementById('task').value = taskPresets[name] || taskPresets.repo;
    }

    function valueOf(id) {
      const element = document.getElementById(id);
      return element ? element.value : '';
    }

    function checked(id) {
      const element = document.getElementById(id);
      return element ? element.checked : false;
    }

    function applyProviderDefaults() {
      const provider = valueOf('provider');
      const defaults = providerDefaults[provider] || providerDefaults.deepseek;
      document.getElementById('model').value = defaults.model;
      document.getElementById('baseUrl').value = defaults.baseUrl;
    }

    document.getElementById('provider').addEventListener('change', applyProviderDefaults);
    updateLayoutControls();
    refreshStatus();
    loadEvidence('evidence');
  </script>
</body>
</html>
"""
