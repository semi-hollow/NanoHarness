from agent_forge.runtime.message import Message
from agent_forge.runtime.llm_client import MockLLMClient
from agent_forge.safety.guardrails import input_guardrail, output_guardrail
from agent_forge.safety.permission import PermissionPolicy, PermissionDecision


class AgentLoop:
    def __init__(self, config, trace, registry, llm=None):
        self.config=config; self.trace=trace; self.registry=registry; self.llm=llm or MockLLMClient("single")

    def run(self, task, agent_name="CodingAgent"):
        g=input_guardrail(task)
        self.trace.add(0,agent_name,"guardrail_check",guardrail={"passed":g.passed,"reason":g.reason})
        if not g.passed:
            return f"blocked: {g.reason}"
        messages=[Message("user",task)]
        policy=PermissionPolicy(self.config.auto_approve_writes)
        tool_history=[]; ran_tests=False; blocked=False
        for step in range(1,self.config.max_steps+1):
            resp=self.llm.chat(messages,self.registry.schemas())
            self.trace.add(step,agent_name,"llm_call",llm_response_summary=resp.content or "tool_calls")
            if not resp.tool_calls:
                final=(resp.content or "")+"\n未验证点: 未进行真实线上压测。"
                og=output_guardrail(final,ran_tests,blocked)
                self.trace.add(step,agent_name,"guardrail_check",guardrail={"passed":og.passed,"reason":og.reason})
                self.trace.add(step,agent_name,"final_answer",observation=final)
                return final
            for tc in resp.tool_calls:
                key=(tc.name,str(tc.arguments))
                if key in tool_history[-3:]:
                    self.trace.add(step,agent_name,"error",success=False,error="repeated tool call")
                    return "blocked: repeated tool call"
                tool_history.append(key)
                action="run_command" if tc.name=="run_command" else ("apply_patch" if tc.name in {"apply_patch","write_file"} else "read")
                decision,reason=policy.decide(action,tc.arguments.get("command","") if tc.arguments else "")
                self.trace.add(step,agent_name,"permission_check",permission_decision=decision.value,tool_call=tc.name)
                if decision==PermissionDecision.DENY:
                    blocked=True
                    obs=f"blocked: {reason}"
                    messages.append(Message("tool",obs,name=tc.name,tool_call_id=tc.id))
                    self.trace.add(step,agent_name,"tool_observation",success=False,observation=obs)
                    continue
                if decision==PermissionDecision.ASK:
                    approved=self.config.auto_approve_writes
                    self.trace.add(step,agent_name,"human_approval",observation="approved" if approved else "rejected")
                    if not approved:
                        blocked=True
                        continue
                obs=self.registry.execute(tc.name,tc.arguments)
                if tc.name=="run_command" and "exit_code=0" in obs.content: ran_tests=True
                self.trace.add(step,agent_name,"tool_call",tool_call=tc.name,tool_arguments=tc.arguments)
                self.trace.add(step,agent_name,"tool_observation",success=obs.success,observation=obs.content)
                messages.append(Message("assistant",f"tool_call:{tc.name}"))
                messages.append(Message("tool",obs.content,name=tc.name,tool_call_id=tc.id))
        return "max steps reached"
