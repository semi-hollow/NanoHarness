from __future__ import annotations

import argparse
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

        if self.path == "/" or self.path.startswith("/index.html"):
            self._send_html(INDEX_HTML)
            return
        if self.path == "/api/status":
            self._send_json(self._status_payload())
            return
        if self.path.startswith("/api/jobs/"):
            job_id = self.path.rsplit("/", 1)[-1]
            job = self.state.get_job(job_id)
            if not job:
                self._send_json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json(_job_to_dict(job))
            return
        if self.path == "/api/latest-report":
            self._send_json({"content": _read_latest_report(self.state.project_dir)})
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
        task = str(payload.get("task") or "fix the failing test in this repository")
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
                "--limit",
                limit,
                "--provider",
                provider,
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

    for pointer_name in ("bench.txt", "run.txt"):
        pointer = project_dir / ".agent_forge/latest" / pointer_name
        if pointer.exists():
            run_dir = Path(pointer.read_text(encoding="utf-8").strip())
            if not run_dir.is_absolute():
                run_dir = project_dir / run_dir
            for name in ("report.md", "usage_report.md"):
                candidate = run_dir / name
                if candidate.exists():
                    return str(candidate)
    return ""


def _read_latest_report(project_dir: Path) -> str:
    """Read the latest report or return a friendly placeholder."""

    path = _latest_report_path(project_dir)
    if not path:
        return "No report yet. Run Mock Agent Run or SWE-bench Sample first."
    return Path(path).read_text(encoding="utf-8")


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
    pre {
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
        <button onclick="startJob('doctor')">Run Doctor</button>
        <button class="secondary" onclick="startJob('verify')">Run Smoke Verify</button>
      </div>
      <div class="card">
        <h2>2. 普通 Agent Run</h2>
        <label>Task</label>
        <textarea id="task">修复 examples/demo_repo 里的测试失败问题</textarea>
        <button onclick="startJob('mock_run')">Mock Agent Run</button>
        <button class="warn" onclick="startJob('deepseek_run')">DeepSeek Agent Run</button>
      </div>
      <div class="card">
        <h2>3. SWE-bench Sample</h2>
        <label>Provider</label>
        <select id="provider">
          <option value="deepseek">deepseek</option>
          <option value="mock">mock</option>
        </select>
        <label>Limit</label>
        <input id="limit" type="number" min="1" max="20" value="1" />
        <button onclick="startJob('swebench_sample')">Run SWE-bench Sample</button>
      </div>
      <div class="card">
        <h2>4. 查看证据</h2>
        <button class="secondary" onclick="startJob('report')">Print Latest Report</button>
        <button class="secondary" onclick="startJob('replay')">Replay Latest Trace</button>
        <button class="secondary" onclick="loadLatestReport()">Load Report File</button>
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
      <pre id="output">Ready. Click Run Doctor first.</pre>
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
        provider: document.getElementById('provider').value,
        limit: Number(document.getElementById('limit').value || 1)
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

    function clearOutput() {
      document.getElementById('output').textContent = '';
      document.getElementById('activeJob').textContent = 'none';
    }

    refreshStatus();
  </script>
</body>
</html>
"""
