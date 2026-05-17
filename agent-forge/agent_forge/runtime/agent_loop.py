from agent_forge.runtime.message import Message
from agent_forge.runtime.llm_client import MockLLMClient
from agent_forge.runtime.planner import SimplePlanner
from agent_forge.runtime.state import AgentState
from agent_forge.runtime.stop_condition import check_stop
from agent_forge.context.context_builder import build_context_report
from agent_forge.context.memory import Memory
from agent_forge.context.repo_map import build_repo_map
from agent_forge.safety.guardrails import input_guardrail, output_guardrail, tool_guardrail
from agent_forge.safety.permission import PermissionPolicy, PermissionDecision


class AgentLoop:
    """Single-agent control loop for context, LLM calls, tools, and trace.

    This is the project's real agent runtime. ``single`` mode calls it
    directly. ``multi`` mode reuses it through ``AgentRuntime`` so supervisor
    workers share the same context, tool, permission, observation, and trace
    semantics.
    """

    def __init__(self, config, trace, registry, llm=None):
        """Receive runtime dependencies from CLI instead of constructing globals."""

        self.config = config
        self.trace = trace
        self.registry = registry
        self.llm = llm or MockLLMClient("single")
        self.planner = SimplePlanner()

    def run(self, task, agent_name="CodingAgent"):
        """Run one task until final answer, guardrail block, or stop condition.

        The loop is deliberately observation-driven: the model proposes a tool
        call, runtime executes it under policy, and the resulting Observation is
        fed back into the next LLM call. That is the key distinction from the
        deterministic workflow demo.
        """

        self.trace.set_run_context(task=task)

        input_check = input_guardrail(task)
        self.trace.add(
            0,
            agent_name,
            "guardrail_check",
            guardrail={
                "category": input_check.category,
                "passed": input_check.passed,
                "reason": input_check.reason,
                "severity": input_check.severity,
            },
        )
        if not input_check.passed:
            self.trace.set_run_context(
                stop_reason="input_guardrail_block",
                final_answer=f"blocked: {input_check.reason}",
            )
            return f"blocked: {input_check.reason}"

        messages = [Message("user", task)]
        state = AgentState(
            task=task,
            workspace_root=self.config.workspace,
            max_iterations=self.config.max_steps,
            messages=messages,
        )
        policy = PermissionPolicy(self.config.auto_approve_writes)
        memory = Memory()
        memory.set("task", task)

        tool_history = []
        ran_tests = False
        blocked = False
        consecutive_failures = 0

        for step in range(1, self.config.max_steps + 1):
            state.iteration = step
            repo_map = build_repo_map(self.config.workspace)
            schemas = self.registry.schemas()
            context_report = build_context_report(
                task,
                repo_map,
                memory,
                docs=repo_map.splitlines(),
                root=self.config.workspace,
                tools=schemas,
            )
            self.trace.add(
                step,
                agent_name,
                "context_assembly",
                context={
                    "selected_files": context_report.selected_files,
                    "retrieved_docs_count": len(context_report.retrieved_docs),
                    "memory_summary": context_report.memory_summary,
                    "total_chars": context_report.total_chars,
                    "max_chars": context_report.max_chars,
                    "truncated": context_report.truncated,
                    "available_tools": context_report.available_tools,
                    "permission_summary": context_report.permission_summary,
                },
            )

            plan = self.planner.plan(task, step, context_report)
            self.trace.add(
                step,
                agent_name,
                "plan",
                plan={
                    "goal": plan.goal,
                    "reasoning_summary": plan.reasoning_summary,
                    "next_action": plan.next_action,
                },
            )

            context_message = Message("system", context_report.render())
            messages_for_llm = [context_message] + messages
            response = self.llm.chat(messages_for_llm, schemas)

            if response.error:
                self.trace.add(step, agent_name, "error", success=False, error=str(response.error))
                state.status = "failed"
                state.stop_reason = "invalid_llm_response"
                self.trace.set_run_context(
                    stop_reason=state.stop_reason,
                    final_answer=str(response.error),
                )
                return f"blocked: invalid llm response: {response.error}"

            self.trace.add(
                step,
                agent_name,
                "llm_call",
                llm_request_summary=(
                    f"messages={len(messages_for_llm)} "
                    f"tools={len(schemas)} "
                    f"context_chars={len(context_message.content)}"
                ),
                llm_response_summary=response.content or "tool_calls",
                model_usage=(
                    self.llm.last_usage.to_dict()
                    if hasattr(self.llm, "last_usage") and self.llm.last_usage
                    else {}
                ),
            )

            if not response.tool_calls:
                final_answer = (response.content or "") + "\n未验证点: 未进行真实线上压测。"
                output_check = output_guardrail(final_answer, ran_tests, blocked)
                self.trace.add(
                    step,
                    agent_name,
                    "guardrail_check",
                    guardrail={
                        "category": output_check.category,
                        "passed": output_check.passed,
                        "reason": output_check.reason,
                        "severity": output_check.severity,
                    },
                )
                self.trace.add(step, agent_name, "final_answer", observation=final_answer)
                state.status = "completed"
                state.final_answer = final_answer
                state.stop_reason = "final_answer"
                self.trace.set_run_context(stop_reason=state.stop_reason, final_answer=final_answer)
                return final_answer

            for tool_call in response.tool_calls:
                key = (tool_call.name, str(tool_call.arguments))
                tool_check = tool_guardrail(
                    tool_call.name,
                    tool_call.arguments,
                    exists=self.registry.get(tool_call.name) is not None,
                    repeated=key in tool_history[-3:],
                )
                self.trace.add(
                    step,
                    agent_name,
                    "guardrail_check",
                    guardrail={
                        "category": tool_check.category,
                        "passed": tool_check.passed,
                        "reason": tool_check.reason,
                        "severity": tool_check.severity,
                    },
                )

                if not tool_check.passed and key in tool_history[-3:]:
                    self.trace.add(step, agent_name, "error", success=False, error=tool_check.reason)
                    self.trace.set_run_context(
                        stop_reason="repeated_tool_call",
                        final_answer="blocked: repeated tool call",
                    )
                    return "blocked: repeated tool call"

                tool_history.append(key)
                self.trace.add(
                    step,
                    agent_name,
                    "action",
                    tool_call=tool_call.name,
                    tool_arguments=tool_call.arguments,
                )

                action = self._permission_action(tool_call.name)
                command = tool_call.arguments.get("command", "") if tool_call.arguments else ""
                decision, reason = policy.decide(action, command)
                self.trace.add(
                    step,
                    agent_name,
                    "permission_check",
                    permission_decision=decision.value,
                    tool_call=tool_call.name,
                )

                if decision == PermissionDecision.DENY:
                    blocked = True
                    observation = f"blocked: {reason}"
                    memory.add_observation(observation)
                    messages.append(
                        Message(
                            "tool",
                            observation,
                            name=tool_call.name,
                            tool_call_id=tool_call.id,
                        )
                    )
                    self.trace.add(
                        step,
                        agent_name,
                        "tool_observation",
                        success=False,
                        observation=observation,
                    )
                    continue

                if decision == PermissionDecision.ASK:
                    approved = self.config.auto_approve_writes
                    self.trace.add(
                        step,
                        agent_name,
                        "human_approval",
                        observation="approved" if approved else "rejected",
                    )
                    if not approved:
                        blocked = True
                        memory.add_observation(f"{tool_call.name}: human approval rejected")
                        continue

                observation = self.registry.execute(tool_call.name, tool_call.arguments)
                memory.add_observation(observation)
                if tool_call.name == "run_command" and "exit_code=0" in observation.content:
                    ran_tests = True

                self.trace.add(
                    step,
                    agent_name,
                    "tool_call",
                    tool_call=tool_call.name,
                    tool_arguments=tool_call.arguments,
                )
                self.trace.add(
                    step,
                    agent_name,
                    "tool_observation",
                    success=observation.success,
                    observation=observation.content,
                )
                self.trace.add(
                    step,
                    agent_name,
                    "observation",
                    success=observation.success,
                    observation_summary=observation.content[:300],
                )
                state.observations.append(observation)

                consecutive_failures = 0 if observation.success else consecutive_failures + 1
                stop = check_stop(step, self.config.max_steps, consecutive_failures)
                if stop.should_stop:
                    state.status = "stopped"
                    state.stop_reason = stop.reason
                    self.trace.set_run_context(
                        stop_reason=stop.reason,
                        final_answer=f"blocked: {stop.reason}",
                    )
                    return f"blocked: {stop.reason}"

                messages.append(Message("assistant", f"tool_call:{tool_call.name}"))
                messages.append(
                    Message(
                        "tool",
                        observation.content,
                        name=tool_call.name,
                        tool_call_id=tool_call.id,
                    )
                )

        self.trace.set_run_context(stop_reason="max_steps", final_answer="max steps reached")
        return "max steps reached"

    def _permission_action(self, tool_name: str) -> str:
        """Map concrete tool names to coarse permission-policy actions."""

        if tool_name == "run_command":
            return "run_command"
        if tool_name in {"apply_patch", "write_file"}:
            return "apply_patch"
        return "read"
