import json

from agent_forge.runtime.message import Message
from agent_forge.runtime.llm_client import MockLLMClient
from agent_forge.runtime.planner import SimplePlanner
from agent_forge.runtime.state import AgentState
from agent_forge.runtime.control import StepController
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

        # Phase 0: reject dangerous tasks before the model sees tools. This is
        # not a replacement for tool-level policy; it is the earliest cheap stop.
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

        # `messages` is the durable conversation inside this run. Each tool call
        # appends an assistant tool_call message plus a tool observation message
        # so the next LLM turn can reason from concrete evidence.
        messages = [Message("user", task)]

        # `state` is mostly trace/debug state. It mirrors what a DB-backed agent
        # service would persist for replay or resume.
        state = AgentState(
            task=task,
            workspace_root=self.config.workspace,
            max_iterations=self.config.max_steps,
            messages=messages,
        )
        policy = PermissionPolicy(self.config.auto_approve_writes)

        # Memory is local to this run, with optional resume seed. It is separate
        # from raw chat messages because prompt memory needs compression and
        # topic-shift filtering.
        memory = Memory()
        memory.seed_session(
            previous_task=getattr(self.config, "previous_task", ""),
            session_summary=getattr(self.config, "session_summary", ""),
        )
        memory.set("task", task)

        # StepController owns loop-control policy: retryability, repeated-action
        # detection, timeout, cost budget, and max failure count.
        controller = StepController.from_config(self.config)

        # `tool_history` keeps backward-compatible recent-repeat checks for the
        # guardrail layer; StepController keeps the stricter stable count.
        tool_history = []

        # Output guardrail uses this to prevent "tests passed" hallucinations.
        ran_tests = False

        # Output guardrail uses this to force the final answer to mention blocks.
        blocked = False

        # Kept for readability with older tests; StepController is the main
        # source of failure-budget truth.
        consecutive_failures = 0

        for step in range(1, self.config.max_steps + 1):
            state.iteration = step

            # Phase 1: assemble context every turn, not once. New observations
            # can change memory summary, selected files, and recovery hints.
            repo_map = build_repo_map(self.config.workspace)
            schemas = self.registry.schemas()
            context_report = build_context_report(
                task,
                repo_map,
                memory,
                docs=repo_map.splitlines(),
                root=self.config.workspace,
                tools=schemas,
                max_chars=getattr(self.config, "max_context_chars", 8000),
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
                    "topic_relation": context_report.topic_relation,
                    "inherit_session": context_report.inherit_session,
                    "dropped_context": context_report.dropped_context,
                    "budget_breakdown": context_report.budget_breakdown,
                    "available_tools": context_report.available_tools,
                    "permission_summary": context_report.permission_summary,
                },
            )

            # Phase 2: produce a small trace-only plan summary. This is not a
            # hidden chain-of-thought; it is an auditable runtime explanation.
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

            # Phase 3: ask the model for either final text or tool calls. All
            # provider-specific details are normalized behind ModelGateway/LLMClient.
            response = self.llm.chat(messages_for_llm, schemas)

            if response.error:
                # Model failures are provider failures, not tool failures. The
                # recovery decision is traced separately so you can explain why
                # this run stopped or retried at the gateway layer.
                signal = controller.model_failure(response.error)
                self.trace.add(step, agent_name, "error", success=False, error=str(response.error))
                self.trace.add(
                    step,
                    agent_name,
                    "recovery_decision",
                    success=signal.retryable,
                    failure_kind=signal.kind.value,
                    retryable=signal.retryable,
                    recovery_hint=signal.recovery_hint,
                )
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
                # Phase 4a: final answer path. Output guardrail checks that the
                # answer does not claim validation that did not happen.
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
                # Phase 4b: action path. First catch repeated intent before any
                # side-effectful tool can run.
                repeat_signal = controller.record_tool_intent(tool_call)
                key = (tool_call.name, str(tool_call.arguments))
                tool_check = tool_guardrail(
                    tool_call.name,
                    tool_call.arguments,
                    exists=self.registry.get(tool_call.name) is not None,
                    repeated=repeat_signal is not None or key in tool_history[-3:],
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

                if repeat_signal is not None:
                    self.trace.add(step, agent_name, "error", success=False, error=tool_check.reason)
                    self.trace.add(
                        step,
                        agent_name,
                        "recovery_decision",
                        success=False,
                        failure_kind=repeat_signal.kind.value,
                        retryable=repeat_signal.retryable,
                        recovery_hint=repeat_signal.recovery_hint,
                    )
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

                # Permission works on coarse action classes instead of concrete
                # tool names. This lets many write-like tools share one policy.
                decision, reason = policy.decide(action, command)
                self.trace.add(
                    step,
                    agent_name,
                    "permission_check",
                    permission_decision=decision.value,
                    tool_call=tool_call.name,
                )

                if decision == PermissionDecision.DENY:
                    # A denial is fed back as an observation. The model should
                    # adapt or stop; it should never silently bypass policy.
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
                    signal = controller.classify_observation(memory.recent_observations()[-1])
                    if signal:
                        self.trace.add(
                            step,
                            agent_name,
                            "recovery_decision",
                            success=signal.retryable,
                            failure_kind=signal.kind.value,
                            retryable=signal.retryable,
                            recovery_hint=signal.recovery_hint,
                        )
                    continue

                if decision == PermissionDecision.ASK:
                    # In this local harness, "approval" is represented by the
                    # auto_approve flag. A real product would call a UI/human
                    # approval service here.
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

                # Phase 5: execute the tool through the registry. The registry
                # validates schema and concrete tools enforce sandbox/policy.
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

                # Phase 6: classify failure and write a recovery decision. This
                # is the part that turns "tool failed" into an explainable next
                # step such as reread file, fix args, or stop.
                signal = controller.classify_observation(observation)
                if signal:
                    memory.add(f"recovery:{signal.kind.value}:{signal.recovery_hint}")
                    self.trace.add(
                        step,
                        agent_name,
                        "recovery_decision",
                        success=signal.retryable,
                        failure_kind=signal.kind.value,
                        retryable=signal.retryable,
                        recovery_hint=signal.recovery_hint,
                    )

                estimated_cost = 0.0
                if hasattr(self.llm, "last_usage") and self.llm.last_usage:
                    estimated_cost = float(getattr(self.llm.last_usage, "estimated_cost_usd", 0.0) or 0.0)

                # Phase 7: enforce budget after each observation, when we know
                # whether the last action made progress.
                stop_signal = controller.should_stop(step, estimated_cost_usd=estimated_cost)
                if stop_signal is not None:
                    state.status = "stopped"
                    state.stop_reason = stop_signal.reason
                    self.trace.set_run_context(
                        stop_reason=stop_signal.reason,
                        final_answer=f"blocked: {stop_signal.reason}",
                    )
                    return f"blocked: {stop_signal.reason}"

                # Phase 8: append protocol-correct tool messages. The next LLM
                # turn sees both the assistant tool call and the tool result.
                messages.append(
                    Message(
                        "assistant",
                        "",
                        tool_calls=[
                            {
                                "id": tool_call.id,
                                "type": "function",
                                "function": {
                                    "name": tool_call.name,
                                    "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                                },
                            }
                        ],
                    )
                )
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
