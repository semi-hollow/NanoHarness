from .llm_client import MockLLMClient
from agent_forge.tools.registry import ToolRegistry
from agent_forge.safety.permission import decide, PermissionDecision

class AgentLoop:
    def __init__(self, config, trace, registry:ToolRegistry, llm=None):
        self.config=config; self.trace=trace; self.registry=registry; self.llm=llm or MockLLMClient()
    def run(self,task,agent_name="CodingAgent"):
        messages=[]
        for i in range(self.config.max_steps):
            out=self.llm.plan_single(task,messages)
            self.trace.add(step=i,agent_name=agent_name,event_type="llm_call",data={"out":out})
            if "final" in out:
                self.trace.add(step=i,agent_name=agent_name,event_type="final_answer",data={"answer":out['final']})
                return out['final']
            t=self.registry.get(out['tool'])
            if not t: raise ValueError('unknown tool')
            action="run_command" if out['tool']=="run_command" else ("write" if out['tool'] in {"write_file","apply_patch"} else "read")
            d,r=decide(action,self.config.auto_approve_writes,out.get('args',{}).get('command',''))
            self.trace.add(step=i,agent_name=agent_name,event_type="permission_check",data={"tool":out['tool'],"decision":d.value,"reason":r})
            if d==PermissionDecision.DENY:
                self.trace.add(step=i,agent_name=agent_name,event_type="error",success=False,error=r)
                return f"blocked: {r}"
            obs=t.execute(**out['args'])
            self.trace.add(step=i,agent_name=agent_name,event_type="tool_call",data={"tool":out['tool'],"args":out['args']})
            self.trace.add(step=i,agent_name=agent_name,event_type="tool_observation",data={"observation":str(obs)[:500]})
            messages.append(str(obs))
        return "max steps reached"
