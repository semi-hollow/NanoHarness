import argparse
from pathlib import Path
from .runtime.config import RuntimeConfig
from .runtime.agent_loop import AgentLoop
from .runtime.llm_client import MockLLMClient
from .observability.trace import TraceRecorder
from .safety.sandbox import WorkspaceSandbox
from .tools.registry import ToolRegistry
from .tools.list_files import ListFilesTool
from .tools.read_file import ReadFileTool
from .tools.write_file import WriteFileTool
from .tools.grep import GrepTool
from .tools.apply_patch import ApplyPatchTool
from .tools.run_command import RunCommandTool
from .tools.git_status import GitStatusTool
from .tools.git_diff import GitDiffTool
from .tools.ask_human import AskHumanTool
from .agents.supervisor_agent import SupervisorAgent
from .workflows.coding_workflow import run_workflow


def reset_demo_repo(workspace: str):
    p = Path(workspace) / "examples/demo_repo/src/calculator.py"
    p.write_text("def add(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8")


def build_registry(workspace: str, auto: bool):
    sandbox = WorkspaceSandbox(workspace)
    r = ToolRegistry()
    for t in [ListFilesTool(sandbox), ReadFileTool(sandbox), WriteFileTool(sandbox), GrepTool(sandbox), ApplyPatchTool(sandbox), RunCommandTool(sandbox), GitStatusTool(sandbox), GitDiffTool(sandbox), AskHumanTool(auto)]:
        r.register(t)
    return r


def main():
    p=argparse.ArgumentParser()
    p.add_argument("task", nargs="?", default="修复 examples/demo_repo 里的测试失败问题")
    p.add_argument("--workspace", default=".")
    p.add_argument("--mode", choices=["single","multi","workflow"], default="single")
    p.add_argument("--llm", default="mock")
    p.add_argument("--max-steps", type=int, default=12)
    p.add_argument("--trace-file", default="agent_forge_trace.json")
    p.add_argument("--no-auto-approve", action="store_true")
    a=p.parse_args()
    reset_demo_repo(a.workspace)
    trace=TraceRecorder(a.trace_file)
    auto=not a.no_auto_approve
    if a.mode=="multi":
        print(SupervisorAgent().run(trace,a.task))
    elif a.mode=="workflow":
        print(run_workflow(a.task))
    else:
        cfg=RuntimeConfig(workspace=a.workspace,max_steps=a.max_steps,auto_approve_writes=auto,trace_file=a.trace_file)
        print(AgentLoop(cfg,trace,build_registry(a.workspace,auto),MockLLMClient("single")).run(a.task))
    trace.write()

if __name__=="__main__":
    main()
