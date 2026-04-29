import argparse
from .runtime.config import RuntimeConfig
from .observability.trace import TraceRecorder
from .tools.registry import ToolRegistry
from .tools.read_file import ReadFileTool
from .tools.apply_patch import ApplyPatchTool
from .tools.run_command import RunCommandTool
from .agents.supervisor_agent import SupervisorAgent
from .runtime.agent_loop import AgentLoop

def build_registry(workspace,auto=True):
    r=ToolRegistry()
    for t in [ReadFileTool(workspace),ApplyPatchTool(workspace),RunCommandTool(workspace)]: r.register(t)
    return r

def main():
    p=argparse.ArgumentParser()
    p.add_argument('task',nargs='?',default='修复 examples/demo_repo 里的测试失败问题')
    p.add_argument('--workspace',default='.')
    p.add_argument('--mode',choices=['single','multi','workflow'],default='single')
    p.add_argument('--llm',default='mock')
    p.add_argument('--max-steps',type=int,default=12)
    p.add_argument('--trace-file',default='agent_forge_trace.json')
    p.add_argument('--no-auto-approve',action='store_true')
    a=p.parse_args()
    cfg=RuntimeConfig(workspace=a.workspace,max_steps=a.max_steps,auto_approve_writes=not a.no_auto_approve,trace_file=a.trace_file)
    tr=TraceRecorder(a.trace_file)
    if a.mode=='multi':
      print(SupervisorAgent().run(tr))
    elif a.mode=='workflow':
      print('plan -> code -> test -> review -> final')
    else:
      print(AgentLoop(cfg,tr,build_registry(a.workspace)).run(a.task))
    tr.write()
