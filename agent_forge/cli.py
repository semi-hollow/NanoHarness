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
from .runtime.execution_environment import ExecutionEnvironment, ExecutionEnvironmentConfig
from .runtime.llm_config import LLMConfig, resolve_llm_config
from .runtime.llm_client import MockLLMClient, OpenAICompatibleLLMClient
from .runtime.session import SessionStore
from .runtime.task_state import TaskStateStore, replay_trace
from .models.gateway import ModelGateway, RetryPolicy
from .observability.trace import TraceRecorder
from .observability.metrics import summarize
from .observability.usage_report import write_usage_artifacts
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
from .tools.mcp_config import MCPConfigLoader
from .agents.supervisor_agent import SupervisorAgent
from .workflows.coding_workflow import run_workflow
from .workflows.review_workflow import run_review


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


WEBHOOK_BUGGY_HANDLER = """from .signature import verify_signature


def handle_webhook(payload: dict, headers: dict, store, queue) -> dict:
    if not verify_signature(payload, headers):
        return {"status": "unauthorized", "code": 401}

    event_id = payload["event_id"]
    event_type = payload["type"]

    # BUG: duplicate event_id is not checked before side effects.
    store.insert_event(event_id, event_type, payload)
    queue.enqueue(event_id, event_type)

    return {"status": "accepted", "code": 200}
"""


def task_targets_webhook_bench(task: str) -> bool:
    """Return whether a task should start from the webhook fixture bug."""

    lowered = task.lower()
    return "webhook_service_repo" in lowered or "webhookpatchbench" in lowered


def reset_webhook_bench(workspace: str) -> None:
    """Reset WebhookPatchBench so repeated demo runs start from the same bug.

    The benchmark is committed in a buggy state on purpose. Resetting here makes
    repeated `local_scripts/run_webhook_deepseek.sh` runs comparable even after
    a previous successful agent run patched the handler.
    """

    repo = Path(workspace) / "examples/webhook_service_repo"
    path = repo / "src/webhook_handler.py"
    path.write_text(WEBHOOK_BUGGY_HANDLER, encoding="utf-8")
    os.utime(path, (time.time() + 2, time.time() + 2))
    for cache_dir in repo.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)


def build_registry(
    workspace: str,
    auto: bool,
    mcp_config_file: str | None = None,
    mcp_allowed_tools: list[str] | None = None,
) -> ToolRegistry:
    """Create the tool registry used by single and multi-agent modes.

    The registry is the tool gateway. Centralizing tool registration here makes
    it easy to answer "which actions can the agent perform?" in a technical walkthrough.
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
    if mcp_config_file:
        registry.mcp_config_report = MCPConfigLoader(sandbox).load_into(
            registry,
            mcp_config_file,
            allowed_tools=mcp_allowed_tools,
        )
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
    parser.add_argument("--mode", choices=["single", "multi", "workflow", "review"], default="single")
    parser.add_argument(
        "--llm",
        choices=["mock", "deepseek", "openai", "openai-compatible"],
        default=os.getenv("AGENT_FORGE_DEFAULT_LLM", "mock"),
        help="LLM provider. Use deepseek for personal online runs; use mock for offline/company verification.",
    )
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
    parser.add_argument("--task-state-root", default=".agent_forge/task_state")
    parser.add_argument("--no-session", action="store_true")
    parser.add_argument("--list-sessions", action="store_true")
    parser.add_argument("--show-run", help="Print report.md for a previous session id.")
    parser.add_argument("--rollback-run", help="Restore files from a previous session rollback bundle.")
    parser.add_argument("--resume-run", help="Seed context from a previous session id.")
    parser.add_argument("--list-task-states", action="store_true")
    parser.add_argument("--show-task-state", help="Print one task-state checkpoint JSON.")
    parser.add_argument("--resume-state", help="Seed context from a task-state checkpoint id.")
    parser.add_argument("--replay-run", help="Print a compact timeline from a trace JSON file.")
    parser.add_argument("--execution-env", choices=["local", "worktree"], default="local")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--cleanup-worktree", action="store_true")
    parser.add_argument(
        "--approval-mode",
        choices=["trusted", "on-write", "on-risk", "locked", "dry-run"],
        default="trusted",
        help="Runtime approval posture for tool execution.",
    )
    parser.add_argument("--mcp-config", help="Load local MCP-style tools from a JSON config file.")
    parser.add_argument(
        "--mcp-allowed-tool",
        action="append",
        default=[],
        help="Allow one configured MCP-style tool; can be repeated.",
    )
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
    task_store = TaskStateStore(args.task_state_root)

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

    if args.list_task_states:
        for checkpoint in task_store.list()[:20]:
            task = checkpoint.task.replace("\n", " ")[:90]
            print(f"{checkpoint.run_id} {checkpoint.status} step={checkpoint.current_step} task={task}")
        return

    if args.show_task_state:
        path = task_store.path_for(args.show_task_state)
        print(path.read_text(encoding="utf-8") if path.exists() else f"task state not found: {args.show_task_state}")
        return

    if args.replay_run:
        print(replay_trace(args.replay_run))
        return

    previous_task = ""
    session_summary = ""
    if args.resume_run:
        # Resume seeds context from a previous report; it does not replay old
        # actions. ContextStrategy later decides whether to inherit it.
        previous = store.load(args.resume_run)
        previous_task = previous.task
        session_summary = store.summary_for_resume(args.resume_run)

    if args.resume_state:
        checkpoint = task_store.load(args.resume_state)
        previous_task = previous_task or checkpoint.task
        state_summary = task_store.resume_summary(args.resume_state)
        session_summary = f"{session_summary}\n{state_summary}".strip()

    session = None
    run_dir = None
    if not args.no_session:
        # Sessions create the auditable artifact folder. If the user did not
        # choose a trace path, put trace.json inside that run directory.
        session, run_dir = store.start(args.workspace, args.mode, args.task)
        if args.trace_file == "agent_forge_trace.json":
            args.trace_file = str(run_dir / "trace.json")

    # Allow scripts to place trace files under .agent_forge/latest/... without
    # requiring every caller to pre-create the directory.
    Path(args.trace_file).parent.mkdir(parents=True, exist_ok=True)
    trace = TraceRecorder(
        args.trace_file,
        verbose=args.verbose_trace,
        write_summary_file=args.write_summary,
    )
    auto_approve = not args.no_auto_approve
    environment = ExecutionEnvironment(
        ExecutionEnvironmentConfig(
            mode=args.execution_env,
            workspace=args.workspace,
            run_id=trace.run_id,
            network_policy="allow" if args.allow_network else "deny",
            keep_worktree=not args.cleanup_worktree,
        )
    )
    environment_probe = environment.prepare()
    active_workspace = str(environment.active_workspace)
    trace.add(
        0,
        "Runtime",
        "execution_environment",
        execution_environment=environment_probe.to_dict(),
    )

    if args.mode in {"single", "multi"} and (Path(active_workspace) / "examples/demo_repo/src/calculator.py").exists():
        reset_demo_repo(active_workspace)
        if task_targets_webhook_bench(args.task):
            reset_webhook_bench(active_workspace)

    diff_tracker = DiffTracker(active_workspace)
    # Capture before any mode runs so report/rollback can explain side effects.
    diff_tracker.capture_before()
    registry = build_registry(active_workspace, auto_approve, args.mcp_config, args.mcp_allowed_tool)
    if hasattr(registry, "mcp_config_report"):
        trace.add(
            0,
            "Runtime",
            "mcp_tools_loaded",
            mcp_config_report=registry.mcp_config_report.to_dict(),
        )

    # Multi mode uses the production-shaped boundary:
    # supervisor -> task graph -> ownership/artifacts -> role specs ->
    # reusable AgentLoop runtime.
    if args.mode == "multi":
        print(SupervisorAgent(workspace=active_workspace).run(trace, args.task, registry))
    elif args.mode == "workflow":
        # workflow mode is intentionally deterministic. It proves the shape of a
        # plan-code-test-review state machine without LLM calls or tool
        # observations, so it should not be read as an intelligent agent path.
        workflow_state = run_workflow(args.task)
        trace.set_run_context(stop_reason=workflow_state.final_status, final_answer=str(workflow_state))
        print(workflow_state)
    elif args.mode == "review":
        report = run_review(active_workspace, trace, args.task)
        print(report.render())
    else:
        # single mode is the canonical runtime path: it assembles context, calls
        # the selected LLM, validates tool calls, executes tools through the
        # registry, feeds observations back, and writes trace evidence.
        runtime_config = RuntimeConfig(
            workspace=active_workspace,
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
            execution_environment=environment,
            task_state_root=args.task_state_root,
            resume_state=args.resume_state or "",
            approval_mode=args.approval_mode,
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
            print(f"{llm_config.provider} LLM config is incomplete; falling back to MockLLMClient.")
            llm = ModelGateway(MockLLMClient("single"), provider="mock", model="mock-single")

        loop = AgentLoop(runtime_config, trace, registry, llm)
        print(loop.run(args.task))

    trace.write()
    usage_json_path, usage_report_path = write_usage_artifacts(args.trace_file)
    if run_dir is not None and session is not None:
        # Persist report artifacts after trace is written. The report gives a
        # human summary; metrics/diff/usage remain machine-readable.
        diff_summary = diff_tracker.summarize_after()
        diff_tracker.write_rollback_bundle(run_dir, diff_summary.changed_files)
        environment.write_manifest(run_dir)
        RunReportWriter(run_dir).write(
            task=args.task,
            mode=args.mode,
            trace_path=args.trace_file,
            diff=diff_summary,
            final_answer=trace.final_answer,
            metrics=summarize(trace.events),
        )
        store.finish(session, run_dir, trace.final_answer, args.trace_file)
    elif usage_report_path.exists():
        print(f"Usage report: {usage_report_path}")

    environment.cleanup()


if __name__ == "__main__":
    main()
