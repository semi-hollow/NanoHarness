from __future__ import annotations

import argparse
import html
import json
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


@dataclass
class UiJob:
    """One background UI action.

    The UI deliberately exposes fixed actions instead of arbitrary shell input.
    That keeps the browser useful for demos without turning it into a local
    command-execution console.
    """

    id: str
    title: str
    command: list[str]
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

    def start_job(self, title: str, command: list[str]) -> UiJob:
        """Start a background subprocess and keep its output available."""

        job = UiJob(id=uuid.uuid4().hex[:10], title=title, command=command)
        with self.lock:
            self.jobs[job.id] = job
        thread = threading.Thread(target=self._run_job, args=(job,), daemon=True)
        thread.start()
        return job

    def _run_job(self, job: UiJob) -> None:
        """Run one fixed command and capture combined stdout/stderr."""

        try:
            process = subprocess.run(
                job.command,
                cwd=self.project_dir,
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
            title, command = _action_to_command(action, payload)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        job = self.state.start_job(title, command)
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


def _action_to_command(action: str, payload: dict[str, Any]) -> tuple[str, list[str]]:
    """Translate a safe UI action into a fixed command list."""

    python = sys.executable
    if action == "doctor":
        return "Doctor", [python, "-m", "agent_forge", "doctor"]
    if action == "verify":
        return "Smoke Verify", ["bash", "scripts/verify.sh"]
    if action == "mock_run":
        task = str(payload.get("task") or "修复 examples/demo_repo 里的测试失败问题")
        return "Mock Agent Run", [python, "-m", "agent_forge", "run", task, "--provider", "mock"]
    if action == "deepseek_run":
        task = str(payload.get("task") or "inspect this repository and improve the requested code safely")
        return "DeepSeek Agent Run", [python, "-m", "agent_forge", "run", task, "--provider", "deepseek"]
    if action == "swebench_sample":
        limit = str(int(payload.get("limit") or 1))
        provider = str(payload.get("provider") or "deepseek")
        return (
            "SWE-bench Sample",
            [
                python,
                "-m",
                "agent_forge",
                "bench",
                "swebench",
                "--showcase",
                "--limit",
                limit,
                "--provider",
                provider,
                "--max-steps",
                "24",
                "--max-context-chars",
                "18000",
                "--direct-baseline",
            ],
        )
    if action == "report":
        return "Latest Report", [python, "-m", "agent_forge", "report", "latest"]
    if action == "replay":
        return "Latest Replay", [python, "-m", "agent_forge", "replay", "latest"]
    raise ValueError(f"unsupported action: {action}")


def _job_to_dict(job: UiJob) -> dict[str, Any]:
    """Serialize one job for the browser."""

    return {
        "id": job.id,
        "title": job.title,
        "command": " ".join(job.command),
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
    demo. These views intentionally summarize the same source artifacts into
    cards, tables, badges, and step timelines.
    """

    if kind == "summary":
        return _render_result_summary(project_dir)
    if kind == "usage":
        return _render_usage_dashboard(project_dir)
    if kind == "timeline":
        return _render_trace_timeline(project_dir)
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
    traces = sorted(run_dir.glob("cases/*/trace.json"))
    return traces[0] if traces else None


def _latest_usage_path(project_dir: Path) -> Path | None:
    """Return the most relevant usage.json for the latest run."""

    run_dir = _latest_run_dir(project_dir)
    if not run_dir:
        return None
    direct = run_dir / "usage.json"
    if direct.exists():
        return direct
    usages = sorted(run_dir.glob("cases/*/usage.json"))
    return usages[0] if usages else None


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
            f"<td>{_badge(case.get('evaluation_status', ''), _tone_for_status(case.get('evaluation_status', '')))}</td>"
            f"<td>{int(case.get('patch_chars') or 0)}</td>"
            "</tr>"
            for case in cases
        )
        case_rows_html = case_rows or "<tr><td colspan='5'>No cases</td></tr>"
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
            "<p>默认样例固定为 <span class='mono'>astropy__astropy-12907</span>：真实 Astropy nested CompoundModel separability bug。它足够复杂，可以稳定暴露上下文检索、工具选择、循环控制、成本统计的改进效果。</p>",
            f"<p><span class='label'>Latest report</span><span class='mono'>{_escape(report_path)}</span></p>",
            "<h3>Cases</h3>",
            "<table><thead><tr><th>instance</th><th>repo</th><th>agent status</th><th>eval status</th><th>patch chars</th></tr></thead>"
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
      .metric-grid, .split { grid-template-columns: 1fr; }
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
        <h2>1. 环境检查</h2>
        <div class="help">
          检查 Python、Git、DeepSeek API Key、datasets、Docker、SWE-bench harness 是否可用。
          这是展示前第一步，用来证明本机环境已经接好。
          <span class="command">python -m agent_forge doctor</span>
        </div>
        <button onclick="startJob('doctor')">Run Doctor</button>
      </div>
      <div class="card">
        <h2>2. DeepSeek 真实 Agent Run</h2>
        <div class="help">
          使用 DeepSeek V4 Flash 跑当前仓库里的真实 AgentLoop：组装上下文、调用模型、选择工具、执行动作、写 trace 和 usage report。
          这条链路适合给面试官展示“真实模型 + 真实工具治理 + 可观测证据”。
          <span class="command">python -m agent_forge run "&lt;task&gt;" --provider deepseek</span>
        </div>
        <label>Task</label>
        <textarea id="task">检查当前仓库的 AgentLoop 调用链，给出一个小而安全的代码改进，并保留 trace 和 usage 证据。</textarea>
        <button class="primary" onclick="startJob('deepseek_run')">Run DeepSeek Agent</button>
      </div>
      <div class="card">
        <h2>3. SWE-bench 真实样例</h2>
        <div class="help">
          固定运行 <span class="mono">astropy__astropy-12907</span>：Astropy nested CompoundModel separability bug。
          固定 case 的好处是每次改 harness 后都能对比同一个复杂样例的 trace、token、工具效率和 patch 结果。
          <span class="command">python -m agent_forge bench swebench --showcase --provider deepseek --direct-baseline</span>
        </div>
        <button class="primary" onclick="startJob('swebench_sample')">Run SWE-bench with DeepSeek</button>
      </div>
      <div class="card">
        <h2>4. 查看运行证据</h2>
        <div class="help">
          这些按钮不会直接 dump 原始 JSON 或命令行日志，而是把运行产物整理成适合展示的卡片、表格和时间线。
        </div>
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
          <span class="command">python -m agent_forge replay latest</span>
        </div>
        <button class="secondary" onclick="loadEvidence('timeline')">Show Trace Timeline</button>
        <details>
          <summary>调试时查看原始报告</summary>
          <button class="secondary" onclick="loadEvidence('raw_report')">Load Raw Report</button>
        </details>
      </div>
      <div class="card">
        <h2>离线兜底</h2>
        <div class="help">
          Mock 只用于公司网络不可用、CI、或不想调用外部模型时的健康检查。正式展示优先跑上面的 DeepSeek/SWE-bench。
        </div>
        <details>
          <summary>展开离线按钮</summary>
          <div class="help">
            本地 smoke check，会用 MockLLM 验证包导入、CLI、基础运行链路，不代表真实模型效果。
            <span class="command">bash scripts/verify.sh</span>
          </div>
          <button class="secondary" onclick="startJob('verify')">Run Offline Smoke Verify</button>
          <div class="help">
            离线 Agent run，只用于确认工具链没有坏。
            <span class="command">python -m agent_forge run "&lt;task&gt;" --provider mock</span>
          </div>
          <button class="secondary" onclick="startJob('mock_run')">Run Offline Mock Agent</button>
        </details>
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
        task: document.getElementById('task').value,
        provider: 'deepseek',
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
        `$ ${job.command}`,
        '',
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

    refreshStatus();
  </script>
</body>
</html>
"""
