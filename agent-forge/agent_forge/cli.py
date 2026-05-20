"""CLI composition layer for the three demo execution modes.

The three modes are intentionally not equal in sophistication:

* ``single`` is the real agent-runtime path. It builds ``AgentLoop`` and shows
  the full context -> LLM -> tool -> observation cycle.
* ``multi`` is a supervised orchestration path. It schedules AgentRuntime-backed
  workers through a conflict-aware task graph with explicit file ownership and
  artifact contracts.
* ``workflow`` is a deterministic baseline. It exists to contrast fixed control
  flow with an observation-driven agent loop.

This distinction is project context, not obvious Python mechanics, so it lives
here where readers first encounter mode dispatch.
"""

import argparse
import os
import shutil
import time
from pathlib import Path

from .runtime.config import RuntimeConfig
from .runtime.agent_loop import AgentLoop
from .runtime.llm_config import LLMConfig, resolve_llm_config
from .runtime.llm_client import MockLLMClient, OpenAICompatibleLLMClient
from .runtime.session import SessionStore
from .models.gateway import ModelGateway, RetryPolicy
from .observability.trace import TraceRecorder
from .observability.metrics import summarize
from .production.diff_tracker import DiffTracker
from .production.run_report import RunReportWriter
from .safety.sandbox import WorkspaceSandbox
from .tools.registry import ToolRegistry
from .tools.list_files import ListFilesTool
from .tools.read_file import ReadFileTool
from .tools.write_file import WriteFileTool
from .tools.grep import GrepSearchTool, GrepTool
from .tools.apply_patch import ApplyPatchTool
from .tools.run_command import RunCommandTool
from .tools.git_status import GitStatusTool
from .tools.git_diff import GitDiffTool
from .tools.ask_human import AskHumanTool
from .tools.diagnostics import DiagnosticsTool
from .agents.supervisor_agent import SupervisorAgent
from .workflows.coding_workflow import run_workflow


def reset_demo_repo(workspace: str) -> None:
    """Reset the tiny demo repository so repeated runs start from the same bug.

    This keeps local demos deterministic. Without resetting, a previous
    successful patch would make the next run look like it "fixed" nothing.
    """

    path = Path(workspace) / "examples/demo_repo/src/calculator.py"
    path.write_text("def add(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8")

    # Bump mtime and clear pycache so unittest never reads stale bytecode after
    # the agent patches the tiny demo repo quickly.
    os.utime(path, (time.time() + 2, time.time() + 2))
    for cache_dir in (Path(workspace) / "examples/demo_repo").rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)


def build_registry(workspace: str, auto: bool) -> ToolRegistry:
    """Create the tool registry used by single and multi-agent modes.

    The registry is the tool gateway. Centralizing tool registration here makes
    it easy to answer "which actions can the agent perform?" in an interview.
    """

    sandbox = WorkspaceSandbox(workspace)
    registry = ToolRegistry()

    # All tools share the same sandbox boundary. Role-specific allowlists are
    # layered later by AgentRuntime/FilteredToolRegistry.
    tools = [
        ListFilesTool(sandbox),
        ReadFileTool(sandbox),
        WriteFileTool(sandbox, auto),
        GrepTool(sandbox),
        GrepSearchTool(sandbox),
        ApplyPatchTool(sandbox, auto),
        RunCommandTool(sandbox, auto),
        GitStatusTool(sandbox),
        GitDiffTool(sandbox),
        DiagnosticsTool(sandbox),
        AskHumanTool(auto),
    ]
    for tool in tools:
        registry.register(tool)
    return registry


def build_llm(config: LLMConfig):
    """Instantiate the concrete LLM client selected by resolved config.

    AgentLoop depends on the ModelGateway interface, not provider-specific HTTP
    clients. This is what makes mock, Ollama, company APIs, and online APIs
    interchangeable without changing runtime logic.
    """

    if config.provider == "mock":
        return ModelGateway(
            MockLLMClient("single"),
            provider="mock",
            model="mock-single",
            retry_policy=RetryPolicy(max_attempts=1),
        )
    if config.uses_openai_compatible_api:
        return ModelGateway(
            OpenAICompatibleLLMClient.from_config(config),
            provider=config.provider,
            model=config.model or "unknown",
            retry_policy=RetryPolicy(max_attempts=2),
        )
    raise ValueError(f"Unsupported LLM provider: {config.provider}")


def build_parser() -> argparse.ArgumentParser:
    """Define CLI flags for mode selection, LLM config, and trace output.

    These flags are also documentation for runtime control: context budget,
    failure budget, repeated-action budget, timeout, and session resume are
    explicit knobs, not hidden constants.
    """

    parser = argparse.ArgumentParser(description="Run Agent Forge demos and workflows.")
    parser.add_argument("task", nargs="?", default="修复 examples/demo_repo 里的测试失败问题")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--mode", choices=["single", "multi", "workflow"], default="single")
    parser.add_argument("--llm", choices=["mock", "openai", "openai-compatible"], default="mock")
    parser.add_argument("--llm-profile", help="Named profile from llm_profiles.json.")
    parser.add_argument("--llm-profile-file", help="Path to a JSON file containing LLM profiles.")
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL, for example http://localhost:11434/v1.")
    parser.add_argument("--api-key", help="OpenAI-compatible API key. Prefer env vars for real secrets.")
    parser.add_argument("--model", help="OpenAI-compatible model name.")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--max-context-chars", type=int, default=8000)
    parser.add_argument("--max-consecutive-failures", type=int, default=3)
    parser.add_argument("--max-tool-repeats", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--cost-budget-usd", type=float)
    parser.add_argument("--trace-file", default="agent_forge_trace.json")
    parser.add_argument("--session-root", default=".agent_forge/runs")
    parser.add_argument("--no-session", action="store_true")
    parser.add_argument("--list-sessions", action="store_true")
    parser.add_argument("--show-run", help="Print report.md for a previous session id.")
    parser.add_argument("--rollback-run", help="Restore files from a previous session rollback bundle.")
    parser.add_argument("--resume-run", help="Seed context from a previous session id.")
    parser.add_argument("--no-auto-approve", action="store_true")
    parser.add_argument("--verbose-trace", action="store_true")
    parser.add_argument("--write-summary", action="store_true")
    return parser


def main() -> None:
    """CLI entry point: compose dependencies, choose mode, write trace.

    This function is intentionally the composition root. Business logic belongs
    in AgentLoop/Supervisor/Tools; CLI only wires config, sessions, diff tracking,
    model gateway, and trace artifacts together.
    """

    args = build_parser().parse_args()
    store = SessionStore(args.session_root)

    if args.list_sessions:
        # Session commands are read-only inspection paths and should not start a
        # new run or modify the demo repo.
        for run_dir in store.list_sessions()[:20]:
            print(run_dir.name)
        return

    if args.show_run:
        report = store.report_path(args.show_run)
        print(report.read_text(encoding="utf-8") if report.exists() else f"run report not found: {args.show_run}")
        return

    if args.rollback_run:
        restored = store.rollback(args.rollback_run, args.workspace)
        print(f"restored {len(restored)} files")
        for path in restored:
            print(path)
        return

    previous_task = ""
    session_summary = ""
    if args.resume_run:
        # Resume seeds context from a previous report; it does not replay old
        # actions. ContextStrategy later decides whether to inherit it.
        previous = store.load(args.resume_run)
        previous_task = previous.task
        session_summary = store.summary_for_resume(args.resume_run)

    if args.workspace == "." and args.mode in {"single", "multi"}:
        reset_demo_repo(args.workspace)

    session = None
    run_dir = None
    if not args.no_session:
        # Sessions create the auditable artifact folder. If the user did not
        # choose a trace path, put trace.json inside that run directory.
        session, run_dir = store.start(args.workspace, args.mode, args.task)
        if args.trace_file == "agent_forge_trace.json":
            args.trace_file = str(run_dir / "trace.json")

    diff_tracker = DiffTracker(args.workspace)
    # Capture before any mode runs so report/rollback can explain side effects.
    diff_tracker.capture_before()
    trace = TraceRecorder(
        args.trace_file,
        verbose=args.verbose_trace,
        write_summary_file=args.write_summary,
    )
    auto_approve = not args.no_auto_approve

    # Multi mode uses the production-shaped boundary:
    # supervisor -> task graph -> ownership/artifacts -> role specs ->
    # reusable AgentLoop runtime.
    if args.mode == "multi":
        print(SupervisorAgent(workspace=args.workspace).run(trace, args.task, build_registry(args.workspace, auto_approve)))
    elif args.mode == "workflow":
        # workflow mode is intentionally deterministic. It proves the shape of a
        # plan-code-test-review state machine without LLM calls or tool
        # observations, so it should not be read as an intelligent agent path.
        workflow_state = run_workflow(args.task)
        trace.set_run_context(stop_reason=workflow_state.final_status, final_answer=str(workflow_state))
        print(workflow_state)
    else:
        # single mode is the canonical runtime path: it assembles context, calls
        # the selected LLM, validates tool calls, executes tools through the
        # registry, feeds observations back, and writes trace evidence.
        runtime_config = RuntimeConfig(
            workspace=args.workspace,
            max_steps=args.max_steps,
            auto_approve_writes=auto_approve,
            trace_file=args.trace_file,
            max_context_chars=args.max_context_chars,
            max_consecutive_failures=args.max_consecutive_failures,
            max_tool_repeats=args.max_tool_repeats,
            timeout_seconds=args.timeout_seconds,
            cost_budget_usd=args.cost_budget_usd,
            previous_task=previous_task,
            session_summary=session_summary,
        )
        llm_config = resolve_llm_config(
            provider=args.llm,
            profile=args.llm_profile,
            profile_file=args.llm_profile_file,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            timeout=args.timeout,
        )
        llm = build_llm(llm_config)
        if llm_config.provider != "mock" and not llm_config.is_configured():
            print("OpenAI-compatible LLM config is incomplete; falling back to MockLLMClient.")
            llm = ModelGateway(MockLLMClient("single"), provider="mock", model="mock-single")

        loop = AgentLoop(runtime_config, trace, build_registry(args.workspace, auto_approve), llm)
        print(loop.run(args.task))

    trace.write()
    if run_dir is not None and session is not None:
        # Persist report artifacts after trace is written. The report gives a
        # human summary; metrics/diff remain machine-readable.
        diff_summary = diff_tracker.summarize_after()
        diff_tracker.write_rollback_bundle(run_dir)
        RunReportWriter(run_dir).write(
            task=args.task,
            mode=args.mode,
            trace_path=args.trace_file,
            diff=diff_summary,
            final_answer=trace.final_answer,
            metrics=summarize(trace.events),
        )
        store.finish(session, run_dir, trace.final_answer, args.trace_file)


if __name__ == "__main__":
    main()
