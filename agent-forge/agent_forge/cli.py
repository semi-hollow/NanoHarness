import argparse
from pathlib import Path

from .runtime.config import RuntimeConfig
from .runtime.agent_loop import AgentLoop
from .runtime.llm_config import LLMConfig, resolve_llm_config
from .runtime.llm_client import MockLLMClient, OpenAICompatibleLLMClient
from .observability.trace import TraceRecorder
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
from .agents.supervisor_agent import SupervisorAgent
from .workflows.coding_workflow import run_workflow


def reset_demo_repo(workspace: str) -> None:
    """Reset the tiny demo repository so repeated runs start from the same bug."""

    path = Path(workspace) / "examples/demo_repo/src/calculator.py"
    path.write_text("def add(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8")


def build_registry(workspace: str, auto: bool) -> ToolRegistry:
    """Create the tool registry used by single and multi-agent modes."""

    sandbox = WorkspaceSandbox(workspace)
    registry = ToolRegistry()
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
        AskHumanTool(auto),
    ]
    for tool in tools:
        registry.register(tool)
    return registry


def build_llm(config: LLMConfig):
    """Instantiate the concrete LLM client selected by resolved config."""

    if config.provider == "mock":
        return MockLLMClient("single")
    if config.uses_openai_compatible_api:
        return OpenAICompatibleLLMClient.from_config(config)
    raise ValueError(f"Unsupported LLM provider: {config.provider}")


def build_parser() -> argparse.ArgumentParser:
    """Define CLI flags for mode selection, LLM config, and trace output."""

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
    parser.add_argument("--trace-file", default="agent_forge_trace.json")
    parser.add_argument("--no-auto-approve", action="store_true")
    return parser


def main() -> None:
    """CLI entry point: compose dependencies, choose mode, write trace."""

    args = build_parser().parse_args()

    if args.workspace == "." and args.mode in {"single", "multi"}:
        reset_demo_repo(args.workspace)

    trace = TraceRecorder(args.trace_file)
    auto_approve = not args.no_auto_approve

    if args.mode == "multi":
        print(SupervisorAgent().run(trace, args.task, build_registry(args.workspace, auto_approve)))
    elif args.mode == "workflow":
        print(run_workflow(args.task))
    else:
        runtime_config = RuntimeConfig(
            workspace=args.workspace,
            max_steps=args.max_steps,
            auto_approve_writes=auto_approve,
            trace_file=args.trace_file,
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
            llm = MockLLMClient("single")

        loop = AgentLoop(runtime_config, trace, build_registry(args.workspace, auto_approve), llm)
        print(loop.run(args.task))

    trace.write()


if __name__ == "__main__":
    main()
