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
from pathlib import Path
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

    def __init__(self, project_dir: Path):
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
            command = _action_to_command(action, payload)
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
            "latest_report": _latest_report_path(self.state.project_dir),
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


def _action_to_command(action: str, payload: dict[str, Any]) -> UiCommand:
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
    command.extend(["--output-root", _payload_text(payload, "outputRoot", ".agent_forge/runs")])

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
        for name in ("report.md", "usage_report.md"):
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
    if kind == "interview":
        return _render_interview_evidence(project_dir)
    if kind == "compare":
        return _render_compare_dashboard(project_dir)
    if kind == "raw_report":
        return f"<pre class='raw-text'>{_escape(_read_latest_report(project_dir))}</pre>"
    return _empty_evidence(f"Unsupported evidence view: {kind}")


def _latest_run_dir(project_dir: Path) -> Path | None:
    """Resolve the newest evidence run directory.

    `scripts/verify.sh` writes a lightweight smoke run under
    `.agent_forge/verify/runs` and updates `latest/run.txt`. That run is useful
    for health checks, but it does not contain SWE-bench comparison artifacts.
    If `latest/bench.txt` is stale, prefer real runs under `.agent_forge/runs`
    before falling back to the verify pointer.
    """

    latest = project_dir / ".agent_forge/latest"
    bench_run = _run_dir_from_pointer(project_dir, latest / "bench.txt")
    if bench_run:
        return bench_run
    runs_dir = project_dir / ".agent_forge/runs"
    if runs_dir.exists():
        candidates = [path for path in runs_dir.iterdir() if path.is_dir()]
        if candidates:
            return max(candidates, key=lambda path: path.stat().st_mtime)
    return _run_dir_from_pointer(project_dir, latest / "run.txt")


def _run_dir_from_pointer(project_dir: Path, pointer: Path) -> Path | None:
    """Resolve one latest pointer, ignoring stale paths."""

    if not pointer.exists():
        return None
    run_dir = Path(pointer.read_text(encoding="utf-8").strip())
    if not run_dir.is_absolute():
        run_dir = project_dir / run_dir
    return run_dir if run_dir.exists() else None


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
        "<p class='help strong'>这里回答面试官最常问的工程量化问题：一次真实运行花了多少 token、多少钱、哪里消耗上下文、工具是否高效。</p>",
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


def _render_trace_timeline(project_dir: Path) -> str:
    """Render the latest trace as a step-by-step visual timeline."""

    trace_path = _latest_trace_path(project_dir)
    trace = _read_json_file(trace_path)
    if not trace:
        return _empty_evidence("No trace.json found. Run DeepSeek Agent or SWE-bench showcase first.")

    grouped: dict[int, list[dict[str, Any]]] = {}
    for event in trace.get("events") or []:
        grouped.setdefault(int(event.get("step") or 0), []).append(event)

    step_blocks = []
    for step, events in sorted(grouped.items()):
        pills = []
        failures = 0
        for index, event in enumerate(events, start=1):
            event_type = str(event.get("event_type") or "")
            success = bool(event.get("success", True))
            failures += 0 if success else 1
            label = _format_trace_event_label(index, event)
            pills.append(f"<span class='event-pill { _event_tone(event_type, success) }'>{_escape(label)}</span>")
        step_blocks.append(
            "<div class='timeline-step'>"
            f"<div class='timeline-head'><strong>Step {step}</strong>{_badge('failed events: ' + str(failures), 'bad') if failures else _badge('all events succeeded', 'ok')}</div>"
            f"<div>{''.join(pills)}</div>"
            "</div>"
        )

    trace_scope = _trace_scope_label(trace_path)
    body = [
        "<h2>执行时间线：AgentLoop 每一步发生了什么</h2>",
        f"<p class='help strong'>当前展示：{_escape(trace_scope)}。按 trace.json 记录顺序展示；从上到下是 step 顺序，同一个 step 内从左到右是事件发生顺序。</p>",
        "<div class='legend-row'>"
        "<span class='legend-item blue'>蓝色表示模型/规划</span>"
        "<span class='legend-item purple'>紫色表示上下文/路由</span>"
        "<span class='legend-item ok'>绿色表示工具或检查成功</span>"
        "<span class='legend-item bad'>红色表示失败</span>"
        "<span class='legend-item neutral'>灰色表示普通记录</span>"
        "</div>",
        _metric_grid(
            [
                ("Run", trace.get("run_id", ""), "trace run id", "neutral"),
                ("Source", trace_scope, "single/multi/verify 来源", "neutral"),
                ("Stop", trace.get("stop_reason", ""), "停止原因", _tone_for_status(trace.get("stop_reason", ""))),
                ("Events", str(len(trace.get("events") or [])), "trace 事件数", "neutral"),
                ("Steps", str(len(grouped)), "AgentLoop 步数", "neutral"),
            ]
        ),
        "<h3>Timeline</h3>",
        "".join(step_blocks),
        f"<p><span class='label'>trace.json</span><span class='mono'>{_escape(str(trace_path))}</span></p>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_interview_evidence(project_dir: Path) -> str:
    """Render the shortest interview-facing evidence path.

    Raw artifacts are useful to debug, but too expensive for an interview. This
    view converts the same trace/usage/comparison files into a five-minute
    narrative: what was run, which runtime boundaries were exercised, where
    single-agent and multi-agent differ, and how failures are explained.
    """

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

    artifact_paths = [
        ("run dir", run_dir),
        ("comparison.json", comparison_path),
        ("multi_agent_summary.json", multi_path),
        ("usage.json", usage_path),
        ("trace.json", trace_path),
    ]
    path_rows = "".join(
        "<tr>"
        f"<td>{_escape(label)}</td>"
        f"<td class='mono'>{_escape(path if path else 'not found')}</td>"
        "</tr>"
        for label, path in artifact_paths
    )

    body = [
        "<h2>Interview Evidence｜面试展示总览</h2>",
        "<p class='help strong'>面试展示总览：先讲闭环，再讲 runtime，再讲 evidence，而不是让面试官读 raw JSON。</p>",
        _metric_grid(
            [
                ("Task", str(task_id)[:90], "本次展示对象", "neutral"),
                ("Single", str(single_status), "单 Agent 结果", _tone_for_status(str(single_status))),
                ("Multi", str(multi_status), "多 Agent / reviewer-verifier 结果", _tone_for_status(str(multi_status))),
                ("Revision", str(revision_rounds), "review/verifier 触发的修订轮数", "neutral"),
                ("Cost", f"${cost:.6f}", "最新 usage 估算成本", "ok"),
                ("Failure", str(failure), "失败归因分类", _tone_for_status(str(failure))),
            ]
        ),
        "<h3>Golden Demo Capsule</h3>",
        "<p class='help'>这块是面试专用胶囊：先帮你讲清项目证明什么，再把学习顺序、核心代码和 evidence 面板连起来。"
        "没有最新 artifact 时也能按离线路线讲；有 artifact 时再补真实 run 证据。</p>",
        "<table><thead><tr><th>capsule</th><th>what to show</th><th>safe claim</th></tr></thead><tbody>",
        "<tr><td>Runtime control plane</td><td>AgentLoop Runtime Flow + Trace Timeline</td><td>复杂度在 runtime，不只靠 prompt。</td></tr>",
        "<tr><td>Evidence loop</td><td>Result Summary + Usage Dashboard</td><td>能解释成本、工具、失败，而不是只给最终答案。</td></tr>",
        "<tr><td>Multi-agent workflow</td><td>Role Timeline + Artifact Handoff Graph</td><td>roles 通过 artifact 交接，不自由聊天。</td></tr>",
        "<tr><td>Evaluation honesty</td><td>Single vs Multi + Failure Taxonomy</td><td>candidate patch 不等于 official resolved rate。</td></tr>",
        "</tbody></table>",
        "<h3>30 分钟学习路径</h3>",
        "<table><thead><tr><th>time</th><th>open this</th><th>why</th></tr></thead><tbody>",
        "<tr><td>0-5 min</td><td class='mono'>docs/technical-defense/learn/三十分钟面试准备包.md</td><td>先记住定位、边界和展示顺序。</td></tr>",
        "<tr><td>5-10 min</td><td class='mono'>docs/technical-defense/demo/五分钟面试演示脚本.md</td><td>照着 5 分钟路线讲，不临场组织。</td></tr>",
        "<tr><td>10-18 min</td><td class='mono'>docs/technical-defense/learn/核心代码阅读路线图.md</td><td>只读最小 7 文件核心版，降低代码学习成本。</td></tr>",
        "<tr><td>18-24 min</td><td class='mono'>docs/technical-defense/demo/evidence/演示证据目录说明.md</td><td>知道 evidence 能证明什么，不能夸大什么。</td></tr>",
        "<tr><td>24-30 min</td><td class='mono'>docs/technical-defense/defense/AI智能体项目面试问答.md</td><td>准备高频追问和防守话术。</td></tr>",
        "</tbody></table>",
        "<h3>5 分钟 Demo</h3>",
        "<ol class='talking-list'>",
        "<li><strong>入口：</strong>用 <span class='mono'>forge ui</span> 打开浏览器 workbench，说明参数来自页面，不需要现场背 CLI。</li>",
        "<li><strong>闭环：</strong>运行固定 SWE-bench reference case，展示真实 issue、clean checkout、patch、trace、usage、report。</li>",
        "<li><strong>Runtime：</strong>打开 Trace Timeline，说明 AgentLoop 由 runtime 控制停止条件、预算、权限、工具失败恢复。</li>",
        "<li><strong>成本：</strong>打开 Usage Dashboard，说明 token、cache、context、tool efficiency 如何定位瓶颈。</li>",
        "<li><strong>多 Agent：</strong>说明 Coordinator 顺序调用多个 AgentLoop，靠 artifact handoff，而不是自由聊天。</li>",
        "</ol>",
        "<h3>AgentLoop Runtime Flow</h3>",
        "<div class='flow-strip'>",
        "<span>Context</span><span>LLM Plan</span><span>ToolRouter</span><span>Sandbox / Policy</span><span>Observation</span><span>Trace / Usage</span>",
        "</div>",
        "<p class='help'>面试时强调：复杂度从 prompt 移到 runtime，才能做权限、预算、重试、回放、审计和失败归因。</p>",
        "<h3>Role Timeline</h3>",
        "<table><thead><tr><th>role</th><th>decision</th><th>steps</th><th>artifact</th><th>short evidence</th></tr></thead>"
        f"<tbody>{_render_role_rows(multi)}</tbody></table>",
        "<h3>Artifact Handoff Graph</h3>",
        "<table><thead><tr><th>artifact</th><th>producer</th><th>path</th><th>why it matters</th></tr></thead>"
        f"<tbody>{_render_artifact_rows(multi)}</tbody></table>",
        "<h3>Single vs Multi</h3>",
        "<table><thead><tr><th>dimension</th><th>single-agent</th><th>multi-agent coordinator</th></tr></thead>",
        "<tbody>",
        f"<tr><td>status</td><td>{_badge(str(single_status), _tone_for_status(str(single_status)))}</td><td>{_badge(str(multi_status), _tone_for_status(str(multi_status)))}</td></tr>",
        f"<tr><td>patch generated</td><td>{_escape(comparison.get('single_patch_generated', '-'))}</td><td>{_escape(comparison.get('multi_patch_generated', '-'))}</td></tr>",
        f"<tr><td>LLM calls</td><td>{_escape(comparison.get('single_llm_calls', '-'))}</td><td>{_escape(comparison.get('multi_llm_calls', summary.get('llm_calls', '-')))}</td></tr>",
        f"<tr><td>tool calls</td><td>{_escape(comparison.get('single_tool_calls', '-'))}</td><td>{_escape(comparison.get('multi_tool_calls', summary.get('tool_calls', '-')))}</td></tr>",
        "</tbody></table>",
        "<p class='help'>不要声称 multi-agent 一定更强。正确说法是：它增加 reviewer/verifier 控制点，代价是更多 token 和延迟，是否上线看 benchmark/eval tradeoff。</p>",
        "<h3>Safety & Failure Taxonomy</h3>",
        "<table><thead><tr><th>risk</th><th>runtime answer</th><th>interview wording</th></tr></thead>",
        "<tbody>",
        "<tr><td>危险命令</td><td>CommandPolicy + PermissionPolicy</td><td>高风险操作不能只靠 prompt，必须 runtime 拦截。</td></tr>",
        "<tr><td>越权写文件</td><td>WorkspaceSandbox + protected paths</td><td>工具执行必须先过路径边界和权限策略。</td></tr>",
        "<tr><td>死循环</td><td>max steps / budget / repeated-call checks</td><td>停止条件由 runtime 兜底，模型只能提出意图。</td></tr>",
        "<tr><td>不可解释失败</td><td>failure taxonomy + trace replay</td><td>失败要能归因到 provider、context、tool、policy、eval。</td></tr>",
        "</tbody></table>",
        "<h3>Interview Talking Points</h3>",
        "<ul class='talking-list'>",
        "<li>Agent Forge 的重点不是多写几个 tool，而是把 coding agent 运行时拆成可控、可审计、可评估的 control plane。</li>",
        "<li>AgentLoop 是 canonical runtime；multi-agent 只是 coordinator 对多个 AgentLoop 的编排，不复制 runtime 逻辑。</li>",
        "<li>Artifacts 是 agent 间通信边界，避免自由聊天导致上下文污染和责任不清。</li>",
        "<li>SWE-bench 是效果闭环，trace/usage/failure taxonomy 是工程闭环。</li>",
        "<li>当前边界：本地单机、顺序 coordinator、没有宣称官方 resolved rate，适合展示核心工程设计而不是 SaaS 平台。</li>",
        "</ul>",
        "<h3>Artifact Paths</h3>",
        f"<table><tbody>{path_rows}</tbody></table>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_compare_dashboard(project_dir: Path) -> str:
    """Render a dedicated single-agent vs multi-agent evidence view.

    The interview page answers "how do I present the project?". This page
    answers a narrower engineering question: what changed when we wrapped the
    same AgentLoop in a coordinator with reviewer/verifier roles.
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
        "<h3>面试时怎么讲</h3>",
        "<ul class='talking-list'>",
        "<li>不要声称 multi-agent 一定更强；正确说法是它增加 reviewer/verifier 控制点，用更高成本换可审计性和可防守性。</li>",
        "<li>同一个 AgentLoop 被复用，multi-agent 的价值在 coordinator、role spec、artifact handoff 和 revision budget。</li>",
        "<li>如果 single 也能生成 patch，就如实说：这个 case 上 single 更便宜；multi 的价值是降低高风险任务的未审查输出概率。</li>",
        "<li>如果要上线，需要继续看 official evaluation、badcase taxonomy 和成本收益，而不是只看 patch_generated。</li>",
        "</ul>",
        "<h3>Reviewer / Verifier Evidence</h3>",
        "<table><tbody>",
        f"<tr><td>verifier status</td><td>{_escape(verifier_status)}</td></tr>",
        f"<tr><td>reviewer findings</td><td>{reviewer_text}</td></tr>",
        f"<tr><td>recommendation</td><td>{_escape(recommendation)}</td></tr>",
        "</tbody></table>",
        "<h3>Artifact Paths</h3>",
        "<table><tbody>",
        f"<tr><td>run dir</td><td class='mono'>{_escape(str(run_dir or 'not found'))}</td></tr>",
        f"<tr><td>comparison.json</td><td class='mono'>{_escape(str(comparison_path or 'not found'))}</td></tr>",
        f"<tr><td>multi_agent_summary.json</td><td class='mono'>{_escape(str(multi_path or 'not found'))}</td></tr>",
        f"<tr><td>usage.json</td><td class='mono'>{_escape(str(usage_path or 'not found'))}</td></tr>",
        "</tbody></table>",
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
  <title>Agent Forge</title>
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
      background:
        radial-gradient(circle at top left, rgba(10, 132, 255, .12), transparent 32rem),
        linear-gradient(180deg, #fbfbfd 0%, var(--bg) 38%, #f2f4f7 100%);
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
    h1 { margin: 0; font-size: 24px; letter-spacing: 0; }
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
      border-radius: 12px;
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
      border-radius: 16px;
      padding: 14px;
      margin-bottom: 14px;
      box-shadow: 0 18px 50px var(--shadow);
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
      border-radius: 14px;
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
      color: #c5d7f2;
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
      border-radius: 10px;
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
      border-radius: 11px;
      background: var(--blue);
      color: white;
      font-weight: 700;
      padding: 9px 12px;
      margin-top: 10px;
      cursor: pointer;
    }
    button:hover { filter: brightness(1.08); }
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
      border-radius: 16px;
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
      background: rgba(245, 245, 247, .86);
      backdrop-filter: blur(22px) saturate(1.35);
    }
    .view-tabs button {
      width: auto;
      margin: 0;
      padding: 8px 11px;
      font-size: 13px;
      color: var(--text);
      background: rgba(255, 255, 255, .78);
      border: 1px solid var(--line);
    }
    .view-tabs button.active {
      color: var(--accent-strong);
      background: rgba(10, 132, 255, .1);
      border-color: rgba(10, 132, 255, .42);
      box-shadow: inset 0 0 0 1px rgba(10, 132, 255, .08);
    }
    .output {
      white-space: pre-wrap;
      word-break: break-word;
      background: rgba(255, 255, 255, .88);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 22px 26px;
      min-height: calc(100vh - 245px);
      max-height: none;
      overflow: auto;
      color: var(--text);
      box-shadow: 0 18px 44px var(--shadow);
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
      padding: 12px;
      background: rgba(250, 250, 252, .88);
    }
    .metric-card.ok { border-color: rgba(10, 132, 255, .28); }
    .metric-card.warn { border-color: rgba(183, 121, 31, .26); }
    .metric-card.bad { border-color: rgba(215, 0, 21, .24); }
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
      padding: 8px 7px;
      text-align: left;
      vertical-align: top;
    }
    th { color: var(--muted); font-weight: 700; }
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
      border-radius: 8px;
      background: rgba(255, 255, 255, .72);
      padding: 10px 8px;
      text-align: center;
      color: #dce6f3;
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
      border-radius: 7px;
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
      color: #dce6f3;
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
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      .status { grid-template-columns: 1fr; }
      .metric-grid, .split, .form-grid, .form-row, .quick-tasks, .flow-strip, .lane-grid, .mini-flow, .action-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body class="sidebar-collapsed status-collapsed">
  <header>
    <div>
      <div class="eyebrow">Agent Forge Workbench</div>
      <h1>面试展示驾驶舱</h1>
      <div class="subtitle">把真实运行、Single/Multi 对比、trace、usage 和安全边界整理成一条可讲清楚的展示路径。</div>
    </div>
    <div class="header-actions">
      <button id="sidebarToggle" class="secondary" onclick="toggleSidebar()">显示操作面板</button>
      <button id="statusToggle" class="secondary" onclick="toggleStatusBar()">显示状态栏</button>
      <button id="focusToggle" class="primary" onclick="toggleFocusMode()">专注展示</button>
      <div class="project-chip" id="projectDir"></div>
    </div>
  </header>
  <main>
    <aside>
      <div class="card">
        <div class="section-kicker">Recommended path</div>
        <h2>面试展示路径</h2>
        <div class="help">
          展示时优先走这条线：先讲项目证明什么，再打开真实 evidence。这里的按钮只负责“看懂和讲清楚”，不启动昂贵任务。
        </div>
        <div class="route-map">
          <div class="route-step">
            <div class="route-num">1</div>
            <div><div class="route-title">面试总览</div><div class="route-copy">5 分钟讲法、核心亮点、边界和证据入口。</div></div>
          </div>
          <div class="route-step">
            <div class="route-num">2</div>
            <div><div class="route-title">Single vs Multi 对比</div><div class="route-copy">讲清楚为什么需要 coordinator，以及成本 tradeoff。</div></div>
          </div>
          <div class="route-step">
            <div class="route-num">3</div>
            <div><div class="route-title">成本与工具效率</div><div class="route-copy">展示 token、cost、cache、context、tool failure。</div></div>
          </div>
          <div class="route-step">
            <div class="route-num">4</div>
            <div><div class="route-title">执行时间线</div><div class="route-copy">解释 AgentLoop 每一步怎么被 runtime 控制。</div></div>
          </div>
        </div>
        <button class="primary" onclick="loadEvidence('interview')">打开面试总览</button>
        <div class="action-grid">
          <button class="secondary" onclick="loadEvidence('compare')">Single vs Multi 对比</button>
          <button class="secondary" onclick="loadEvidence('summary')">结果摘要</button>
          <button class="secondary" onclick="loadEvidence('usage')">成本与工具效率</button>
          <button class="secondary" onclick="loadEvidence('timeline')">执行时间线</button>
        </div>
      </div>
      <div class="card">
        <div class="section-kicker">Run real evidence</div>
        <h2>真实运行操作</h2>
        <div class="help">
          这里才是真正启动 Agent。展示推荐先跑固定 SWE-bench reference case；日常开发再用下面的自由任务。
        </div>
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
        <label>Agent Mode</label>
        <select id="benchAgentMode">
          <option value="compare">推荐展示：single + multi 分别跑，再生成 comparison</option>
          <option value="multi">只跑多 Agent：coordinator + reviewer/verifier</option>
          <option value="single">只跑单 Agent：canonical AgentLoop</option>
        </select>
        <div class="checkbox-line">
          <input id="directBaseline" type="checkbox" checked />
          <span>同时跑 direct baseline，用来回答“为什么需要 harness 而不是只问一次模型”。</span>
        </div>
        <div class="checkbox-line">
          <input id="officialEvaluate" type="checkbox" />
          <span>调用官方 SWE-bench 评测；需要 Docker 和 swebench 包，耗时更长。</span>
        </div>
        <label>Official Eval Workers</label>
        <input id="maxWorkers" type="number" min="1" max="8" value="1" />
        <button class="primary" onclick="startJob('swebench_sample')">运行固定 SWE-bench 案例</button>
        <details>
          <summary>跑固定回归集，成本更高</summary>
          <div class="help">
            固定运行 3 个真实 SWE-bench cases，用于比较 harness 改动前后的 patch rate、blocked rate、token/cost 和 failure diagnosis。
          </div>
          <button class="secondary" onclick="startJob('swebench_regression')">运行核心回归集</button>
        </details>
      </div>
      <div class="card">
        <div class="section-kicker">Daily agent task</div>
        <h2>日常 Agent 任务</h2>
        <div class="help">用于你自己实际开发和学习，不是面试固定展示路线。</div>
        <label>Task</label>
        <textarea id="task">检查当前仓库的 AgentLoop 调用链，给出一个小而安全的代码改进，并保留 trace 和 usage 证据。</textarea>
        <div class="quick-tasks">
          <button class="secondary" onclick="setTaskPreset('repo')">读懂仓库</button>
          <button class="secondary" onclick="setTaskPreset('fix')">修复问题</button>
          <button class="secondary" onclick="setTaskPreset('refactor')">安全重构</button>
          <button class="secondary" onclick="setTaskPreset('doc')">补充说明</button>
        </div>
        <details>
          <summary>高级：workspace / Skill / MCP / 输出目录</summary>
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
        <button class="primary" onclick="startJob('agent_run')">运行自定义 Agent 任务</button>
      </div>
      <div class="card">
        <div class="section-kicker">Maintenance</div>
        <h2>环境与维护</h2>
        <div class="help">
          Doctor 只检查环境；Verify 是本地 smoke 验证。它们不是面试主证据，避免和 SWE-bench evidence 混淆。
        </div>
        <button class="secondary" onclick="startJob('doctor')">运行 Doctor 环境检查</button>
        <button class="secondary" onclick="startJob('verify')">运行 Verify smoke check</button>
        <details>
          <summary>调试时查看原始报告</summary>
          <button class="secondary" onclick="loadEvidence('raw_report')">查看原始 report.md</button>
        </details>
      </div>
    </aside>
    <section>
      <div class="status">
        <div class="pill"><div class="k">Python</div><div class="v" id="python"></div></div>
        <div class="pill"><div class="k">Latest Report</div><div class="v" id="latestReport"></div></div>
        <div class="pill"><div class="k">Current View</div><div class="v" id="currentView">面试总览</div></div>
        <div class="pill"><div class="k">Active Job</div><div class="v" id="activeJob">none</div></div>
      </div>
      <div class="view-tabs">
        <button data-view="interview" onclick="loadEvidence('interview')">面试总览</button>
        <button data-view="compare" onclick="loadEvidence('compare')">Single vs Multi 对比</button>
        <button data-view="summary" onclick="loadEvidence('summary')">结果摘要</button>
        <button data-view="usage" onclick="loadEvidence('usage')">成本与工具效率</button>
        <button data-view="timeline" onclick="loadEvidence('timeline')">执行时间线</button>
        <button class="ghost" onclick="toggleSidebar()">显示/隐藏操作面板</button>
        <button class="ghost" onclick="toggleStatusBar()">显示/隐藏状态栏</button>
        <button class="ghost" onclick="toggleFocusMode()">专注展示</button>
        <button class="ghost" onclick="refreshStatus()">刷新状态</button>
        <button class="ghost" onclick="clearOutput()">清空输出</button>
      </div>
      <div id="output" class="output">正在加载面试总览...</div>
      <h2 id="jobsTitle" style="font-size:16px">Recent Jobs</h2>
      <div id="jobs"></div>
    </section>
  </main>
  <script>
    let currentJob = null;
    const evidenceTitles = {
      interview: '面试总览',
      compare: 'Single vs Multi 对比',
      summary: '结果摘要',
      usage: '成本与工具效率',
      timeline: '执行时间线',
      raw_report: '原始报告'
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
      document.getElementById('latestReport').textContent = data.latest_report || 'none';
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
        outputRoot: valueOf('outputRoot'),
        skills: valueOf('skills'),
        skillManifests: valueOf('skillManifests'),
        mcpConfig: valueOf('mcpConfig'),
        mcpTools: valueOf('mcpTools'),
        benchAgentMode: valueOf('benchAgentMode'),
        directBaseline: checked('directBaseline'),
        officialEvaluate: checked('officialEvaluate'),
        maxWorkers: valueOf('maxWorkers'),
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
        sidebarToggle.textContent = sidebarHidden ? '显示操作面板' : '隐藏操作面板';
      }
      if (statusToggle) {
        statusToggle.textContent = statusHidden ? '显示状态栏' : '隐藏状态栏';
      }
      if (focusToggle) {
        focusToggle.textContent = focused ? '退出专注' : '专注展示';
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
    loadEvidence('interview');
  </script>
</body>
</html>
"""
