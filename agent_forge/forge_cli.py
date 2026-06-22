from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from agent_forge.bench.swebench import build_swebench_parser, run_swebench_from_args
from agent_forge.observability.trace import TraceRecorder
from agent_forge.observability.usage_report import write_usage_artifacts
from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_config import resolve_llm_config
from agent_forge.runtime.task_state import replay_trace
from agent_forge.runtime.wiring import build_llm, build_registry
from agent_forge.ui import build_ui_parser, run_ui_from_args


def build_parser() -> argparse.ArgumentParser:
    """Build the product-facing command surface.

    The parser exposes user goals instead of internal modes: run a task, run a
    public benchmark, inspect a report, replay a trace, or check setup.
    """

    parser = argparse.ArgumentParser(
        prog="forge",
        description="Agent Forge: a SWE-bench-oriented CodingAgent harness.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run Agent Forge on a repository task.")
    run_parser.add_argument("task", help="Issue or coding task to solve.")
    run_parser.add_argument("--workspace", default=".")
    run_parser.add_argument("--provider", default=os.getenv("AGENT_FORGE_DEFAULT_LLM", "deepseek"))
    run_parser.add_argument("--model")
    run_parser.add_argument("--base-url")
    run_parser.add_argument("--api-key")
    run_parser.add_argument("--max-steps", type=int, default=16)
    run_parser.add_argument("--max-context-chars", type=int, default=12000)
    run_parser.add_argument("--approval-mode", default="trusted", choices=["trusted", "on-write", "on-risk", "locked", "dry-run"])
    run_parser.add_argument("--output-root", default=".agent_forge/runs")

    bench_parser = subparsers.add_parser("bench", help="Run benchmark loops.")
    bench_subparsers = bench_parser.add_subparsers(dest="bench_name", required=True)
    swebench_parser = bench_subparsers.add_parser("swebench", help="Generate SWE-bench predictions.")
    build_swebench_parser(swebench_parser)

    report_parser = subparsers.add_parser("report", help="Print a benchmark or run report.")
    report_parser.add_argument("target", nargs="?", default="latest")

    replay_parser = subparsers.add_parser("replay", help="Replay a trace timeline.")
    replay_parser.add_argument("target", nargs="?", default="latest")

    subparsers.add_parser("doctor", help="Check local benchmark/runtime environment.")
    subparsers.add_parser("tui", help="Open a lightweight terminal menu.")
    ui_parser = subparsers.add_parser("ui", help="Open the local browser demo UI.")
    build_ui_parser(ui_parser)
    return parser


def main(argv: list[str] | None = None) -> None:
    """Dispatch the public CLI."""

    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        print(render_doctor())
        return
    if args.command == "run":
        run_dir = run_repository_task(args)
        print(f"Run directory: {run_dir}")
        print(f"Report: {run_dir / 'usage_report.md'}")
        return
    if args.command == "bench" and args.bench_name == "swebench":
        summary = run_swebench_from_args(args)
        print(f"Benchmark run: {summary.output_dir}")
        print(f"Result card: {summary.output_dir / 'report.md'}")
        print(f"Predictions: {summary.predictions_path}")
        return
    if args.command == "report":
        print_report(args.target)
        return
    if args.command == "replay":
        print(replay_trace(str(resolve_trace_target(args.target))))
        return
    if args.command == "tui":
        run_tui()
        return
    if args.command == "ui":
        run_ui_from_args(args)
        return


def run_repository_task(args: argparse.Namespace) -> Path:
    """Run the canonical AgentLoop on the selected workspace."""

    run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:7]}"
    run_dir = Path(args.output_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "trace.json"
    trace = TraceRecorder(str(trace_path))
    registry = build_registry(args.workspace, auto=True)
    llm_config = resolve_llm_config(
        provider=args.provider,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        timeout=60,
    )
    if args.provider != "mock" and not llm_config.is_configured():
        raise SystemExit(
            f"{args.provider} model config is incomplete. Set provider env vars or use --provider mock."
        )
    llm = build_llm(llm_config)
    config = RuntimeConfig(
        workspace=args.workspace,
        max_steps=args.max_steps,
        trace_file=str(trace_path),
        max_context_chars=args.max_context_chars,
        timeout_seconds=900,
        task_state_root=str(run_dir / "task_state"),
        approval_mode=args.approval_mode,
    )
    final_answer = AgentLoop(config, trace, registry, llm).run(args.task)
    trace.write()
    write_usage_artifacts(trace_path)
    (run_dir / "final_answer.txt").write_text(final_answer, encoding="utf-8")
    _write_latest_run_pointer(run_dir)
    return run_dir


def render_doctor() -> str:
    """Return a concise environment report for benchmark readiness."""

    rows = [
        ("python", sys.version.split()[0]),
        ("platform", f"{platform.system()} {platform.machine()}"),
        ("cwd", str(Path.cwd())),
        ("git", _command_version(["git", "--version"])),
        ("docker", _command_version(["docker", "--version"])),
        ("datasets", "installed" if importlib.util.find_spec("datasets") else "missing; install with python -m pip install -e '.[bench]'"),
        ("swebench", "installed" if importlib.util.find_spec("swebench") else "missing; needed only for official --evaluate"),
        ("DEEPSEEK_API_KEY", "set" if os.getenv("DEEPSEEK_API_KEY") else "missing"),
        ("AGENT_FORGE_BASE_URL", os.getenv("AGENT_FORGE_BASE_URL", "")),
        ("AGENT_FORGE_MODEL", os.getenv("AGENT_FORGE_MODEL", "")),
    ]
    width = max(len(name) for name, _ in rows)
    return "\n".join(f"{name:<{width}} : {value}" for name, value in rows)


def print_report(target: str) -> None:
    """Print report.md for a run directory or the latest benchmark."""

    report = resolve_report_target(target)
    print(report.read_text(encoding="utf-8"))


def resolve_report_target(target: str) -> Path:
    """Resolve ``latest`` or a path to a report file."""

    if target == "latest":
        pointer = Path(".agent_forge/latest/bench.txt")
        if not pointer.exists():
            pointer = Path(".agent_forge/latest/run.txt")
        if not pointer.exists():
            raise SystemExit("No latest run pointer found.")
        target = pointer.read_text(encoding="utf-8").strip()
    path = Path(target)
    if path.is_dir():
        for candidate in (path / "report.md", path / "usage_report.md"):
            if candidate.exists():
                return candidate
    if path.exists():
        return path
    raise SystemExit(f"Report not found: {target}")


def resolve_trace_target(target: str) -> Path:
    """Resolve ``latest`` or a path to trace.json."""

    if target == "latest":
        pointer = Path(".agent_forge/latest/bench.txt")
        if not pointer.exists():
            pointer = Path(".agent_forge/latest/run.txt")
        if not pointer.exists():
            raise SystemExit("No latest run pointer found.")
        run_dir = Path(pointer.read_text(encoding="utf-8").strip())
        if (run_dir / "trace.json").exists():
            return run_dir / "trace.json"
        traces = sorted(run_dir.glob("cases/*/trace.json"))
        if traces:
            return traces[0]
        raise SystemExit(f"No trace found under {run_dir}")
    path = Path(target)
    if path.is_dir():
        return path / "trace.json"
    return path


def run_tui() -> None:
    """A tiny terminal menu for users who do not want to remember commands.

    This is deliberately lightweight. It is not a Claude Code-style product
    surface; it simply makes the main benchmark flow discoverable while keeping
    the repo focused on agent-runtime design.
    """

    print("Agent Forge")
    print("1. Doctor")
    print("2. Run SWE-bench Lite sample")
    print("3. Run a task in current repo")
    print("4. Show latest report")
    choice = input("Choose 1-4: ").strip()
    if choice == "1":
        print(render_doctor())
    elif choice == "2":
        limit = input("Limit [1]: ").strip() or "1"
        provider = input("Provider [deepseek]: ").strip() or "deepseek"
        args = argparse.Namespace(
            dataset="princeton-nlp/SWE-bench_Lite",
            split="test",
            limit=int(limit),
            instance_id=[],
            cases_file=None,
            provider=provider,
            model=None,
            base_url=None,
            api_key=None,
            max_steps=16,
            max_context_chars=12000,
            repo_cache=".agent_forge/bench/repos",
            output_root=".agent_forge/runs",
            direct_baseline=True,
            evaluate=False,
            max_workers=1,
            namespace_empty=False,
        )
        summary = run_swebench_from_args(args)
        print(f"Result card: {summary.output_dir / 'report.md'}")
    elif choice == "3":
        task = input("Task: ").strip()
        if not task:
            print("No task provided.")
            return
        args = argparse.Namespace(
            task=task,
            workspace=".",
            provider=os.getenv("AGENT_FORGE_DEFAULT_LLM", "deepseek"),
            model=None,
            base_url=None,
            api_key=None,
            max_steps=16,
            max_context_chars=12000,
            approval_mode="trusted",
            output_root=".agent_forge/runs",
        )
        print(f"Run directory: {run_repository_task(args)}")
    elif choice == "4":
        print_report("latest")
    else:
        print("Canceled.")


def _command_version(command: list[str]) -> str:
    """Return a command version or a compact missing marker."""

    if shutil.which(command[0]) is None:
        return "missing"
    result = subprocess.run(command, text=True, capture_output=True)
    return (result.stdout or result.stderr).strip().splitlines()[0]


def _write_latest_run_pointer(run_dir: Path) -> None:
    """Update stable pointer for report/replay commands."""

    latest = Path(".agent_forge/latest")
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "run.txt").write_text(str(run_dir), encoding="utf-8")


if __name__ == "__main__":
    main()
