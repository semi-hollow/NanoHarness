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
    if kind == "raw_report":
        return f"<pre class='raw-text'>{_escape(_read_latest_report(project_dir))}</pre>"
    return _empty_evidence(f"Unsupported evidence view: {kind}")


def _latest_run_dir(project_dir: Path) -> Path | None:
    """Resolve the newest benchmark/run directory from stable pointers."""

    latest = project_dir / ".agent_forge/latest"
    for pointer_name in ("bench.txt", "run.txt"):
        pointer = latest / pointer_name
        if pointer.exists():
            run_dir = Path(pointer.read_text(encoding="utf-8").strip())
            if not run_dir.is_absolute():
                run_dir = project_dir / run_dir
            if run_dir.exists():
                return run_dir
    runs_dir = project_dir / ".agent_forge/runs"
    if not runs_dir.exists():
        return None
    candidates = [path for path in runs_dir.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


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
            "<h2>Result Summary</h2>",
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
            "<h2>Result Summary</h2>",
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
        "<h2>Usage Dashboard</h2>",
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
        for event in events:
            event_type = str(event.get("event_type") or "")
            success = bool(event.get("success", True))
            failures += 0 if success else 1
            label = event_type
            if event.get("tool_call"):
                label += f" · {event.get('tool_call')}"
            if event.get("duration_ms"):
                label += f" · {int(event.get('duration_ms') or 0)}ms"
            pills.append(f"<span class='event-pill { _event_tone(event_type, success) }'>{_escape(label)}</span>")
        step_blocks.append(
            "<div class='timeline-step'>"
            f"<div class='timeline-head'><strong>Step {step}</strong>{_badge('failed events: ' + str(failures), 'bad') if failures else _badge('ok', 'ok')}</div>"
            f"<div>{''.join(pills)}</div>"
            "</div>"
        )

    body = [
        "<h2>Trace Timeline</h2>",
        "<p class='help strong'>这张图把 raw trace 转成执行时间线：context 进入模型，模型产生 action，工具执行，observation 回到下一轮。</p>",
        _metric_grid(
            [
                ("Run", trace.get("run_id", ""), "trace run id", "neutral"),
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
        "<h2>Interview Evidence</h2>",
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


def _event_tone(event_type: str, success: bool) -> str:
    """Map trace event type to a compact visual class."""

    if not success:
        return "bad"
    if event_type in {"llm_call", "plan", "planning_mode"}:
        return "blue"
    if event_type in {"tool_call", "tool_observation", "action"}:
        return "warn"
    if event_type in {"guardrail_check", "permission_check", "hook_check"}:
        return "ok"
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
      --bg: #0f1115;
      --panel: #171b22;
      --panel-2: #1e2430;
      --text: #edf2f7;
      --muted: #9aa4b2;
      --line: #2b3340;
      --green: #3ddc97;
      --blue: #6aa9ff;
      --yellow: #ffd166;
      --red: #ff6b6b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      padding: 22px 28px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    h1 { margin: 0; font-size: 24px; letter-spacing: 0; }
    .subtitle { margin-top: 4px; color: var(--muted); font-size: 14px; }
    main {
      display: grid;
      grid-template-columns: 360px 1fr;
      min-height: calc(100vh - 84px);
    }
    aside {
      border-right: 1px solid var(--line);
      padding: 20px;
      background: #12161d;
    }
    section { padding: 20px; }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 14px;
    }
    .card h2 {
      font-size: 15px;
      margin: 0 0 10px;
    }
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
      background: #0d1016;
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
      color: #07111f;
      font-weight: 700;
      padding: 10px 12px;
      margin-top: 10px;
      cursor: pointer;
    }
    button.secondary { background: var(--panel-2); color: var(--text); border: 1px solid var(--line); }
    button.warn { background: var(--yellow); color: #1b1300; }
    button.primary { background: var(--green); color: #06150f; }
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
      grid-template-columns: repeat(3, minmax(0, 1fr));
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
    .output {
      white-space: pre-wrap;
      word-break: break-word;
      background: #090b10;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-height: 360px;
      max-height: 70vh;
      overflow: auto;
      color: #dce6f3;
    }
    .evidence { white-space: normal; color: var(--text); }
    .evidence h2 { margin: 0 0 8px; font-size: 20px; }
    .evidence h3 { margin: 18px 0 8px; font-size: 15px; }
    .strong { color: #dce6f3; }
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
      background: #111620;
    }
    .metric-card.ok { border-color: rgba(61, 220, 151, .45); }
    .metric-card.warn { border-color: rgba(255, 209, 102, .55); }
    .metric-card.bad { border-color: rgba(255, 107, 107, .55); }
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
      background: var(--panel-2);
    }
    .badge.ok, .event-pill.ok { border-color: rgba(61, 220, 151, .5); color: var(--green); }
    .badge.warn, .event-pill.warn { border-color: rgba(255, 209, 102, .55); color: var(--yellow); }
    .badge.bad, .event-pill.bad { border-color: rgba(255, 107, 107, .55); color: var(--red); }
    .event-pill.blue { border-color: rgba(106, 169, 255, .55); color: var(--blue); }
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
      color: #dce6f3;
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
      background: #0d1016;
      padding: 10px 8px;
      text-align: center;
      color: #dce6f3;
      font-size: 12px;
      font-weight: 700;
    }
    .timeline-step {
      border-left: 3px solid var(--line);
      padding: 10px 0 10px 14px;
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
      padding: 5px 8px;
      margin: 0 6px 6px 0;
      font-size: 12px;
      background: #0d1016;
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
    .succeeded { color: var(--green); }
    .failed { color: var(--red); }
    .running { color: var(--yellow); }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      .status { grid-template-columns: 1fr; }
      .metric-grid, .split, .form-grid, .form-row, .quick-tasks, .flow-strip { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Agent Forge</h1>
      <div class="subtitle">SWE-bench-oriented CodingAgent Harness</div>
    </div>
    <div class="subtitle" id="projectDir"></div>
  </header>
  <main>
    <aside>
      <div class="card">
        <h2>环境检查</h2>
        <div class="help">
          检查 Python、Git、DeepSeek API Key、datasets、Docker、SWE-bench harness 是否可用。展示前先点一次，确认本机环境接好。
        </div>
        <button onclick="startJob('doctor')">Run Doctor</button>
      </div>
      <div class="card">
        <h2>CodingAgent Workbench</h2>
        <div class="help">
          在这里配置一次真实 Agent 运行：任务、模型、上下文预算、审批策略、Skill、MCP 都从页面传入，不需要记命令。
        </div>
        <label>Task</label>
        <textarea id="task">检查当前仓库的 AgentLoop 调用链，给出一个小而安全的代码改进，并保留 trace 和 usage 证据。</textarea>
        <div class="quick-tasks">
          <button class="secondary" onclick="setTaskPreset('repo')">读懂仓库</button>
          <button class="secondary" onclick="setTaskPreset('fix')">修复问题</button>
          <button class="secondary" onclick="setTaskPreset('refactor')">安全重构</button>
          <button class="secondary" onclick="setTaskPreset('doc')">补充说明</button>
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
        <button class="primary" onclick="startJob('agent_run')">Run Agent</button>
      </div>
      <div class="card">
        <h2>SWE-bench 闭环</h2>
        <div class="help">
          固定运行 <span class="mono">astropy__astropy-12907</span>，用同一个真实缺陷观察 trace、token、工具效率、patch 结果和 direct baseline 差异。
        </div>
        <label>Agent Mode</label>
        <select id="benchAgentMode">
          <option value="compare">compare: single 和 multi 分别跑，再生成 comparison</option>
          <option value="multi">multi: coordinator + reviewer/verifier</option>
          <option value="single">single: 只跑 AgentLoop</option>
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
        <button class="primary" onclick="startJob('swebench_sample')">Run Reference Case</button>
        <details>
          <summary>跑固定回归集，成本更高</summary>
          <div class="help">
            固定运行 3 个真实 SWE-bench cases，用于比较 harness 改动前后的 patch rate、blocked rate、token/cost 和 failure diagnosis。
          </div>
          <button class="secondary" onclick="startJob('swebench_regression')">Run Core Regression Set</button>
        </details>
      </div>
      <div class="card">
        <h2>运行证据</h2>
        <div class="help">
          这些按钮不会直接 dump 原始 JSON 或命令行日志，而是把运行产物整理成适合展示的卡片、表格和时间线。
        </div>
        <div class="help">
          面试展示总览：5 分钟路线、runtime flow、role timeline、artifact handoff、single vs multi、safety/failure 讲法。
        </div>
        <button class="primary" onclick="loadEvidence('interview')">Show Interview Evidence</button>
        <div class="help">
          看本次 benchmark/run 的结果、case、状态、patch 是否生成、成本摘要。
        </div>
        <button class="secondary" onclick="loadEvidence('summary')">Show Result Summary</button>
        <div class="help">
          看 token、cost、cache、context breakdown、tool efficiency 和 step 级消耗。
        </div>
        <button class="secondary" onclick="loadEvidence('usage')">Show Usage Dashboard</button>
        <div class="help">
          看每一步 context、LLM、action、tool observation、guardrail/permission 的执行时间线。
        </div>
        <button class="secondary" onclick="loadEvidence('timeline')">Show Trace Timeline</button>
        <details>
          <summary>调试时查看原始报告</summary>
          <button class="secondary" onclick="loadEvidence('raw_report')">Load Raw Report</button>
        </details>
      </div>
      <div class="card">
        <h2>本地健康检查</h2>
        <div class="help">
          检查包导入、公开入口、Skill registry、MCP 配置；如果配置了 DeepSeek API key，还会跑一次真实只读 Agent 任务。
        </div>
        <button class="secondary" onclick="startJob('verify')">Run Verify</button>
      </div>
    </aside>
    <section>
      <div class="status">
        <div class="pill"><div class="k">Python</div><div class="v" id="python"></div></div>
        <div class="pill"><div class="k">Latest Report</div><div class="v" id="latestReport"></div></div>
        <div class="pill"><div class="k">Active Job</div><div class="v" id="activeJob">none</div></div>
      </div>
      <div class="tabs">
        <button class="secondary" onclick="refreshStatus()">Refresh</button>
        <button class="secondary" onclick="clearOutput()">Clear Output</button>
      </div>
      <div id="output" class="output">Ready. 建议先点 Run Doctor，再点 Run SWE-bench with DeepSeek，然后看 Result Summary / Usage Dashboard / Trace Timeline。</div>
      <h2 style="font-size:16px">Recent Jobs</h2>
      <div id="jobs"></div>
    </section>
  </main>
  <script>
    let currentJob = null;
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
      refreshStatus();
    }

    function clearOutput() {
      document.getElementById('output').textContent = '';
      document.getElementById('activeJob').textContent = 'none';
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
    refreshStatus();
  </script>
</body>
</html>
"""
