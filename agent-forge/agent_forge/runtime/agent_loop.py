from agent_forge.runtime.message import Message
from agent_forge.runtime.llm_client import MockLLMClient
from agent_forge.runtime.planner import SimplePlanner
from agent_forge.runtime.state import AgentState
from agent_forge.runtime.stop_condition import check_stop
from agent_forge.context.context_builder import build_context_report
from agent_forge.context.memory import Memory
from agent_forge.context.repo_map import build_repo_map
from agent_forge.safety.guardrails import input_guardrail, output_guardrail
from agent_forge.safety.permission import PermissionPolicy, PermissionDecision


class AgentLoop:
    def __init__(self, config, trace, registry, llm=None):
        self.config=config; self.trace=trace; self.registry=registry; self.llm=llm or MockLLMClient("single"); self.planner=SimplePlanner()

    def run(self, task, agent_name="CodingAgent"):
        self.trace.set_run_context(task=task)
        g=input_guardrail(task)
        self.trace.add(0,agent_name,"guardrail_check",guardrail={"passed":g.passed,"reason":g.reason})
        if not g.passed:
            self.trace.set_run_context(stop_reason="input_guardrail_block", final_answer=f"blocked: {g.reason}")
            return f"blocked: {g.reason}"
        messages=[Message("user",task)]
        state=AgentState(task=task,workspace_root=self.config.workspace,max_iterations=self.config.max_steps,messages=messages)
        policy=PermissionPolicy(self.config.auto_approve_writes)
        tool_history=[]; ran_tests=False; blocked=False; consecutive_failures=0
        for step in range(1,self.config.max_steps+1):
            state.iteration=step
            repo_map=build_repo_map(self.config.workspace)
            context_report=build_context_report(task,repo_map,Memory(),docs=repo_map.splitlines(),root=self.config.workspace)
            self.trace.add(step,agent_name,"context_assembly",context={
                "selected_files": context_report.selected_files,
                "total_chars": context_report.total_chars,
                "truncated": context_report.truncated,
            })
            plan=self.planner.plan(task,step,context_report)
            self.trace.add(step,agent_name,"plan",plan={
                "goal": plan.goal,
                "reasoning_summary": plan.reasoning_summary,
                "next_action": plan.next_action,
            })
            resp=self.llm.chat(messages,self.registry.schemas())
            if resp.error:
                self.trace.add(step,agent_name,"error",success=False,error=str(resp.error))
                state.status="failed"; state.stop_reason="invalid_llm_response"
                self.trace.set_run_context(stop_reason=state.stop_reason, final_answer=str(resp.error))
                return f"blocked: invalid llm response: {resp.error}"
            self.trace.add(step,agent_name,"llm_call",llm_response_summary=resp.content or "tool_calls")
            if not resp.tool_calls:
                final=(resp.content or "")+"\n未验证点: 未进行真实线上压测。"
                og=output_guardrail(final,ran_tests,blocked)
                self.trace.add(step,agent_name,"guardrail_check",guardrail={"passed":og.passed,"reason":og.reason})
                self.trace.add(step,agent_name,"final_answer",observation=final)
                state.status="completed"; state.final_answer=final; state.stop_reason="final_answer"
                self.trace.set_run_context(stop_reason=state.stop_reason, final_answer=final)
                return final
            for tc in resp.tool_calls:
                key=(tc.name,str(tc.arguments))
                if key in tool_history[-3:]:
                    self.trace.add(step,agent_name,"error",success=False,error="repeated tool call")
                    self.trace.set_run_context(stop_reason="repeated_tool_call", final_answer="blocked: repeated tool call")
                    return "blocked: repeated tool call"
                tool_history.append(key)
                self.trace.add(step,agent_name,"action",tool_call=tc.name,tool_arguments=tc.arguments)
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
                self.trace.add(step,agent_name,"observation",success=obs.success,observation_summary=obs.content[:300])
                state.observations.append(obs)
                consecutive_failures = 0 if obs.success else consecutive_failures + 1
                stop=check_stop(step,self.config.max_steps,consecutive_failures)
                if stop.should_stop:
                    state.status="stopped"; state.stop_reason=stop.reason
                    self.trace.set_run_context(stop_reason=stop.reason, final_answer=f"blocked: {stop.reason}")
                    return f"blocked: {stop.reason}"
                messages.append(Message("assistant",f"tool_call:{tc.name}"))
                messages.append(Message("tool",obs.content,name=tc.name,tool_call_id=tc.id))
        self.trace.set_run_context(stop_reason="max_steps", final_answer="max steps reached")
        return "max steps reached"
