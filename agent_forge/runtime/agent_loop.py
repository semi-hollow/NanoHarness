import json

from agent_forge.runtime.message import Message
from agent_forge.runtime.approval import ApprovalStore
from agent_forge.runtime.planner import SimplePlanner
from agent_forge.runtime.state import AgentState
from agent_forge.runtime.control import StepController
from agent_forge.runtime.clarification import ClarificationPolicy
from agent_forge.runtime.execution_environment import ExecutionEnvironment, ExecutionEnvironmentConfig
from agent_forge.runtime.hooks import HookContext, HookDecisionType, HookManager
from agent_forge.runtime.observation import Observation
from agent_forge.runtime.planning_mode import PlanningModePolicy
from agent_forge.runtime.task_state import TaskRunStatus, TaskStateStore, summarize_checkpoint
from agent_forge.context.context_builder import build_context_report
from agent_forge.context.memory import Memory
from agent_forge.context.repo_map import build_repo_map
from agent_forge.observability.evidence import EvidenceLedger
from agent_forge.safety.guardrails import input_guardrail, output_guardrail, tool_guardrail
from agent_forge.skills import build_default_skill_registry
from agent_forge.tools.tool_router import ToolRouter


class AgentLoop:
    """Single-agent control loop for context, LLM calls, tools, and trace.

    This is the project's real agent runtime. ``forge run`` and
    ``forge bench swebench`` both call it so normal tasks and benchmark cases
    share the same context, tool, permission, observation, and trace semantics.

    Why it cannot be replaced by a simple function:
        The loop must coordinate mutable state across many boundaries:
        prompt context, model response parsing, tool policy, observations,
        recovery signals, budget checks, evidence, task checkpoints, and final
        answer guardrails. Splitting those concerns into hidden callbacks would
        make the system harder to debug.

    Method map:
        ``__init__`` wires runtime dependencies.
        ``run`` is the only public execution path.
        ``_permission_action`` maps tool names to policy action classes.
        ``_update_task_state`` persists checkpoint changes.
        ``_stop_run`` writes terminal state, hook notifications, and trace
        context.
    """

    def __init__(self, config, trace, registry, llm=None):
        """Receive runtime dependencies from CLI instead of constructing globals."""

        self.config = config
        self.trace = trace
        self.registry = registry
        if llm is None:
            raise ValueError("AgentLoop requires a real LLM client; build it through runtime.wiring.build_llm")
        self.llm = llm
        self.planner = SimplePlanner()
        self.clarification_policy = ClarificationPolicy()
        self.planning_mode_policy = PlanningModePolicy()
        self.tool_router = ToolRouter()
        self.skill_registry = build_default_skill_registry(getattr(config, "skill_manifest_files", []))
        self.environment = getattr(config, "execution_environment", None) or ExecutionEnvironment(
            ExecutionEnvironmentConfig(workspace=config.workspace)
        )
        self.hooks = HookManager.default(
            self.environment,
            getattr(config, "auto_approve_writes", True),
            approval_mode=getattr(config, "approval_mode", "trusted"),
        )
        self.task_state_store = TaskStateStore(getattr(config, "task_state_root", ".agent_forge/task_state"))
        self.approval_store = ApprovalStore(getattr(config, "approval_root", ".agent_forge/approvals"))

    def run(self, task, agent_name="CodingAgent"):
        """Run one task until final answer, guardrail block, or stop condition.

        The loop is deliberately observation-driven: the model proposes a tool
        call, runtime executes it under policy, and the resulting Observation is
        fed back into the next LLM call. That is the key distinction from a
        one-shot prompt baseline.
        """

        self.trace.set_run_context(task=task)
        resume_summary = self._load_resume_summary(agent_name)
        checkpoint = self.task_state_store.start(
            run_id=self.trace.run_id,
            task=task,
            workspace=self.config.workspace,
            agent_name=agent_name,
            metadata={"execution_environment": self.environment.probe().to_dict()},
        )
        self.trace.add(
            0,
            agent_name,
            "task_state_checkpoint",
            task_state=checkpoint.to_dict(),
        )

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
            final_answer = f"blocked: {input_check.reason}"
            self._stop_run(checkpoint, TaskRunStatus.BLOCKED, "input_guardrail_block", final_answer)
            return final_answer

        clarification = self.clarification_policy.decide(task)
        self.trace.add(
            0,
            agent_name,
            "clarification_decision",
            clarification={
                "action": clarification.action,
                "confidence": clarification.confidence,
                "reason": clarification.reason,
                "question": clarification.question,
                "missing_fields": clarification.missing_fields,
            },
        )
        if clarification.action == "refuse":
            final_answer = f"blocked: {clarification.reason}"
            self._stop_run(checkpoint, TaskRunStatus.BLOCKED, "unsupported_task", final_answer)
            return final_answer
        if clarification.needs_user_input():
            final_answer = f"needs clarification: {clarification.question}"
            self._stop_run(checkpoint, TaskRunStatus.WAITING_APPROVAL, "needs_clarification", final_answer)
            return final_answer

        planning_mode = self.planning_mode_policy.decide(task)
        self.trace.add(
            0,
            agent_name,
            "planning_mode",
            planning_mode={
                "mode": planning_mode.mode,
                "reason": planning_mode.reason,
                "complexity": planning_mode.complexity,
            },
        )

        active_skills = self._select_active_skills(task)
        active_skill_cards = [skill.prompt_card() for skill in active_skills]
        skill_tool_names = {tool_name for skill in active_skills for tool_name in skill.tool_names}
        self.trace.add(
            0,
            agent_name,
            "skill_selection",
            skills=[
                {
                    "name": skill.name,
                    "version": skill.version,
                    "tools": skill.tool_names,
                    "entrypoint": skill.entrypoint,
                }
                for skill in active_skills
            ],
            skill_mode=getattr(self.config, "skill_mode", "auto"),
        )

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
        # Memory is local to this run, with optional resume seed. It is separate
        # from raw chat messages because prompt memory needs compression and
        # topic-shift filtering.
        memory = Memory()
        session_summary = getattr(self.config, "session_summary", "")
        if resume_summary:
            session_summary = "\n".join(part for part in [session_summary, resume_summary] if part)
        memory.seed_session(
            previous_task=getattr(self.config, "previous_task", ""),
            session_summary=session_summary,
        )
        memory.set("task", task, scope="session", source="user_task", agent_name=agent_name)
        evidence = EvidenceLedger()

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
            self._update_task_state(
                checkpoint,
                status=TaskRunStatus.RUNNING,
                current_step=step,
                messages_count=len(messages),
                observations_count=len(state.observations),
                resume_hint="Rerun with --resume-state to seed this task state into a continuation.",
            )

            # Phase 1: assemble context every turn, not once. New observations
            # can change memory summary, selected files, and recovery hints.
            repo_map = build_repo_map(self.config.workspace)
            all_schemas = self.registry.schemas()
            route = self.tool_router.route(
                task,
                all_schemas,
                step=step,
                agent_name=agent_name,
                skill_tool_names=skill_tool_names,
            )
            schemas = route.schemas
            force_final_turn = step == self.config.max_steps
            routed_allowed_names = set(route.allowed_names)
            turn_permission_summary = (
                "read/list/grep allowed; write/apply_patch asks approval; "
                "dangerous commands denied; "
                f"{self.environment.describe()}"
            )
            if force_final_turn:
                # On the last allowed step, stop offering tools and ask the
                # model to summarize evidence or name a blocker. This turns
                # max_steps from a hard "blocked" cliff into a useful final
                # response for real interactive use.
                schemas = []
                routed_allowed_names = set()
                turn_permission_summary += (
                    "; final step: no more tool calls are available, provide the best "
                    "evidence-based final answer and clearly mark unverified items"
                )
            context_report = build_context_report(
                task,
                repo_map,
                memory,
                docs=repo_map.splitlines(),
                root=self.config.workspace,
                tools=schemas,
                active_skill_cards=active_skill_cards,
                max_chars=getattr(self.config, "max_context_chars", 8000),
                permission_summary=turn_permission_summary,
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
                    "active_skills": [f"{skill.name}@{skill.version}" for skill in active_skills],
                    "permission_summary": context_report.permission_summary,
                    "tool_routing": {
                        "reason": route.reason,
                        "allowed_tools": sorted(routed_allowed_names),
                        "dropped_tools": route.dropped_names,
                        "metadata": route.metadata,
                    },
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
            tool_schema_chars = sum(len(str(schema)) for schema in schemas)
            history_chars = sum(
                len(message.content or "")
                + len(str(message.tool_calls or ""))
                + len(message.reasoning_content or "")
                for message in messages
            )

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
                final_answer = f"blocked: invalid llm response: {response.error}"
                self._stop_run(
                    checkpoint,
                    TaskRunStatus.FAILED,
                    state.stop_reason,
                    final_answer,
                    current_step=step,
                    resume_hint=signal.recovery_hint,
                )
                return final_answer

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
                llm_input_breakdown_chars={
                    "system_context": len(context_message.content),
                    "conversation_history": history_chars,
                    "tool_schemas": tool_schema_chars,
                },
                model_usage=(
                    self.llm.last_usage.to_dict()
                    if hasattr(self.llm, "last_usage") and self.llm.last_usage
                    else {}
                ),
            )

            if not response.tool_calls:
                # Phase 4a: final answer path. A provider can occasionally leave
                # raw tool-call markup in text on a forced-final turn; treat that
                # as an unfinished action instead of a completed artifact.
                if self._contains_raw_tool_call_markup(response.content or ""):
                    final_answer = "blocked: pending_tool_call_at_stop"
                    self.trace.add(
                        step,
                        agent_name,
                        "final_answer",
                        success=False,
                        observation=final_answer,
                        pending_tool_call=True,
                    )
                    self._stop_run(
                        checkpoint,
                        TaskRunStatus.BLOCKED,
                        "pending_tool_call_at_stop",
                        final_answer,
                        current_step=step,
                        messages_count=len(messages),
                        observations_count=len(state.observations),
                        resume_hint="Increase step budget or keep required tools routed until the pending call executes.",
                    )
                    return final_answer

                # Output guardrail checks that the answer does not claim validation
                # that did not happen.
                citations = evidence.final_citations()
                evidence_text = ""
                if citations:
                    evidence_text = "\n证据:\n" + "\n".join(f"- {item}" for item in citations)
                final_answer = (response.content or "") + evidence_text + "\n未验证点: 未进行真实线上压测。"
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
                self.trace.add(
                    step,
                    agent_name,
                    "final_answer",
                    observation=final_answer,
                    evidence_refs=citations,
                )
                state.status = "completed"
                state.final_answer = final_answer
                state.stop_reason = "final_answer"
                self._stop_run(
                    checkpoint,
                    TaskRunStatus.COMPLETED,
                    state.stop_reason,
                    final_answer,
                    current_step=step,
                    messages_count=len(messages),
                    observations_count=len(state.observations),
                )
                return final_answer

            # Phase 4b: action path. The provider may return multiple tool
            # calls in one assistant message. OpenAI-compatible history must
            # preserve that shape: one assistant message containing all
            # tool_calls, followed by one tool message per tool_call_id.
            messages.append(
                Message(
                    "assistant",
                    "",
                    reasoning_content=response.reasoning_content,
                    tool_calls=[
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }
                        for call in response.tool_calls
                    ],
                )
            )

            for tool_call in response.tool_calls:
                # First catch repeated intent before any
                # side-effectful tool can run.
                repeat_signal = controller.record_tool_intent(tool_call)
                key = (tool_call.name, str(tool_call.arguments))
                tool_check = tool_guardrail(
                    tool_call.name,
                    tool_call.arguments,
                    exists=self.registry.get(tool_call.name) is not None and tool_call.name in routed_allowed_names,
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
                    if self._is_recoverable_repeated_tool(tool_call.name):
                        observation = Observation(
                            tool_call.name,
                            False,
                            f"repeated read-only tool call: {tool_call.name}; use prior observation or choose a different tool",
                        )
                        memory.add_observation(observation)
                        messages.append(
                            Message(
                                "tool",
                                observation.content,
                                name=tool_call.name,
                                tool_call_id=tool_call.id,
                            )
                        )
                        self.trace.add(
                            step,
                            agent_name,
                            "tool_observation",
                            success=False,
                            observation=observation.content,
                        )
                        self.trace.add(
                            step,
                            agent_name,
                            "recovery_decision",
                            success=True,
                            failure_kind=repeat_signal.kind.value,
                            retryable=True,
                            recovery_hint="Use existing read/search evidence, inspect a different symbol, or proceed to apply_patch/git_diff.",
                        )
                        self._update_task_state(
                            checkpoint,
                            status=TaskRunStatus.RUNNING,
                            current_step=step,
                            last_tool=tool_call.name,
                            last_observation=observation.content,
                            resume_hint="Repeated read/search was skipped; continue with different evidence or edit.",
                        )
                        continue

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
                    self._stop_run(
                        checkpoint,
                        TaskRunStatus.BLOCKED,
                        "repeated_tool_call",
                        "blocked: repeated tool call",
                        current_step=step,
                        last_tool=tool_call.name,
                        resume_hint=repeat_signal.recovery_hint,
                    )
                    return "blocked: repeated tool call"

                if tool_call.name not in routed_allowed_names:
                    blocked = True
                    observation = Observation(
                        tool_call.name,
                        False,
                        f"tool not routed for this turn: {tool_call.name}",
                    )
                    memory.add_observation(observation)
                    messages.append(
                        Message(
                            "tool",
                            observation.content,
                            name=tool_call.name,
                            tool_call_id=tool_call.id,
                        )
                    )
                    self.trace.add(
                        step,
                        agent_name,
                        "tool_observation",
                        success=False,
                        observation=observation.content,
                    )
                    signal = controller.classify_observation(observation)
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
                    self._update_task_state(
                        checkpoint,
                        status=TaskRunStatus.BLOCKED,
                        current_step=step,
                        last_tool=tool_call.name,
                        last_observation=observation.content[:600],
                        resume_hint=signal.recovery_hint if signal else "Tool was not available in this routed turn.",
                    )
                    continue

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
                hook_context = HookContext(
                    run_id=self.trace.run_id,
                    step=step,
                    agent_name=agent_name,
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments or {},
                    action=action,
                    command=command,
                    auto_approve_writes=self.config.auto_approve_writes,
                    approval_mode=getattr(self.config, "approval_mode", "trusted"),
                )

                # Hooks are the production-style policy chain. Permission,
                # execution environment, and redaction live here instead of
                # being scattered through the ReAct loop.
                hook_result = self.hooks.pre_tool(hook_context)
                self.trace.add(
                    step,
                    agent_name,
                    "hook_check",
                    hook_result=hook_result.to_dict(),
                    tool_call=tool_call.name,
                )
                self.trace.add(
                    step,
                    agent_name,
                    "permission_check",
                    permission_decision=hook_result.decision.value,
                    tool_call=tool_call.name,
                    reason=hook_result.reason,
                )

                if hook_result.decision == HookDecisionType.DENY:
                    # A denial is fed back as an observation. The model should
                    # adapt or stop; it should never silently bypass policy.
                    blocked = True
                    observation = Observation(tool_call.name, False, f"blocked: {hook_result.reason}")
                    memory.add_observation(observation)
                    messages.append(
                        Message(
                            "tool",
                            observation.content,
                            name=tool_call.name,
                            tool_call_id=tool_call.id,
                        )
                    )
                    self.trace.add(
                        step,
                        agent_name,
                        "tool_observation",
                        success=False,
                        observation=observation.content,
                    )
                    signal = controller.classify_observation(observation)
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
                    self._update_task_state(
                        checkpoint,
                        status=TaskRunStatus.BLOCKED,
                        current_step=step,
                        last_tool=tool_call.name,
                        last_observation=observation.content,
                        resume_hint=signal.recovery_hint if signal else "Action was blocked by runtime policy.",
                    )
                    continue

                if hook_result.decision == HookDecisionType.ASK:
                    operation_key = ApprovalStore.operation_key(
                        tool_call.name,
                        tool_call.arguments or {},
                        self.config.workspace,
                        action,
                    )
                    approval = self.approval_store.get(operation_key)
                    if approval is None and not self.config.auto_approve_writes:
                        approval = self.approval_store.request(
                            tool_name=tool_call.name,
                            arguments=tool_call.arguments or {},
                            action=action,
                            command=command,
                            workspace=self.config.workspace,
                            run_id=self.trace.run_id,
                            step=step,
                            agent_name=agent_name,
                            reason=hook_result.reason,
                        )
                    self._update_task_state(
                        checkpoint,
                        status=TaskRunStatus.WAITING_APPROVAL,
                        current_step=step,
                        last_tool=tool_call.name,
                        resume_hint="Approve this tool action or rerun with a safer task.",
                    )
                    approved = self.config.auto_approve_writes if approval is None else approval.status == "approved"
                    approval_trace = (
                        approval.to_dict()
                        if approval is not None
                        else {
                            "operation_key": operation_key,
                            "status": "auto_approved",
                            "tool_name": tool_call.name,
                            "arguments": tool_call.arguments or {},
                            "action": action,
                        }
                    )
                    self.trace.add(
                        step,
                        agent_name,
                        "human_approval",
                        observation="approved" if approved else approval.status,
                        approval_request=approval_trace,
                    )
                    if approval is not None and approval.status == "pending" and not self.config.auto_approve_writes:
                        final_answer = (
                            f"waiting_approval: {tool_call.name} requires approval before execution. "
                            f"operation_key={approval.operation_key} request={approval.path}"
                        )
                        self._stop_run(
                            checkpoint,
                            TaskRunStatus.WAITING_APPROVAL,
                            "waiting_approval",
                            final_answer,
                            current_step=step,
                            last_tool=tool_call.name,
                            resume_hint=(
                                f"Run `forge approve {approval.operation_key}` then resume or rerun the task."
                            ),
                        )
                        return final_answer
                    if not approved:
                        blocked = True
                        observation = Observation(tool_call.name, False, f"{tool_call.name}: human approval rejected")
                        memory.add_observation(observation)
                        messages.append(
                            Message(
                                "tool",
                                observation.content,
                                name=tool_call.name,
                                tool_call_id=tool_call.id,
                            )
                        )
                        self.trace.add(
                            step,
                            agent_name,
                            "tool_observation",
                            success=False,
                            observation=observation.content,
                        )
                        self._update_task_state(
                            checkpoint,
                            status=TaskRunStatus.WAITING_APPROVAL,
                            current_step=step,
                            last_tool=tool_call.name,
                            last_observation=observation.content,
                            resume_hint="Human approval was rejected; rerun after narrowing the requested edit.",
                        )
                        continue
                    self._update_task_state(checkpoint, status=TaskRunStatus.RUNNING, current_step=step)

                # Phase 5: execute the tool through the registry. The registry
                # validates schema and concrete tools enforce sandbox/policy.
                observation = self.registry.execute(tool_call.name, tool_call.arguments)
                observation = self.hooks.post_tool(hook_context, observation)
                memory.add_observation(observation)
                evidence_item = evidence.add_observation(observation)
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
                if evidence_item:
                    self.trace.add(
                        step,
                        agent_name,
                        "evidence_collected",
                        evidence=evidence_item.citation(),
                    )
                state.observations.append(observation)
                self._update_task_state(
                    checkpoint,
                    status=TaskRunStatus.RUNNING,
                    current_step=step,
                    last_tool=tool_call.name,
                    last_observation=observation.content[:600],
                    messages_count=len(messages),
                    observations_count=len(state.observations),
                )

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
                    final_answer = f"blocked: {stop_signal.reason}"
                    self._stop_run(
                        checkpoint,
                        TaskRunStatus.BLOCKED,
                        stop_signal.reason,
                        final_answer,
                        current_step=step,
                        last_tool=tool_call.name,
                        last_observation=observation.content[:600],
                        resume_hint=stop_signal.recovery_hint,
                    )
                    return final_answer

                # Phase 8: append the tool result that corresponds to the
                # assistant tool_call_id recorded before the loop.
                messages.append(
                    Message(
                        "tool",
                        observation.content,
                        name=tool_call.name,
                        tool_call_id=tool_call.id,
                    )
                )

        self._stop_run(checkpoint, TaskRunStatus.BLOCKED, "max_steps", "blocked: max_steps reached")
        return "blocked: max_steps reached"

    def _contains_raw_tool_call_markup(self, content: str) -> bool:
        """Detect provider text that is really an unexecuted tool request."""

        lowered = content.lower()
        return "tool_calls" in lowered and "invoke name=" in lowered

    def _is_recoverable_repeated_tool(self, tool_name: str) -> bool:
        """Allow read/search repeats to become feedback instead of terminal blocks."""

        return tool_name in {"read_file", "grep", "grep_search", "list_files", "git_status", "git_diff", "diagnostics"}

    def _select_active_skills(self, task: str):
        """Select real coding skills for this run.

        Skill selection lives in AgentLoop because it affects both prompt
        context and tool routing. Keeping it here prevents SkillRegistry from
        becoming a passive catalog that never changes runtime behavior.
        """

        mode = getattr(self.config, "skill_mode", "auto")
        if mode == "none":
            return []
        explicit_names = list(getattr(self.config, "skill_names", []) or [])
        return self.skill_registry.select_for_task(task, names=explicit_names or None, limit=3)

    def _permission_action(self, tool_name: str) -> str:
        """Map concrete tool names to coarse permission-policy actions."""

        if tool_name == "run_command":
            return "run_command"
        if tool_name in {"apply_patch", "write_file"}:
            return "apply_patch"
        return "read"

    def _update_task_state(self, checkpoint, status: TaskRunStatus | None = None, **changes):
        """Persist a compact run checkpoint without cluttering AgentLoop logic."""

        if status is not None:
            changes["status"] = status.value
        return self.task_state_store.update(checkpoint, **changes)

    def _stop_run(
        self,
        checkpoint,
        status: TaskRunStatus,
        stop_reason: str,
        final_answer: str,
        **changes,
    ) -> None:
        """Record a terminal state in trace, task state, and stop hooks."""

        self.trace.set_run_context(stop_reason=stop_reason, final_answer=final_answer)
        self._update_task_state(
            checkpoint,
            status=status,
            stop_reason=stop_reason,
            final_answer=final_answer,
            **changes,
        )
        hook_decisions = self.hooks.on_stop(self.trace.run_id, stop_reason, final_answer)
        self.trace.add(
            changes.get("current_step", 0),
            checkpoint.agent_name,
            "stop_hooks",
            hook_decisions=[decision.to_dict() for decision in hook_decisions],
            stop_reason=stop_reason,
        )

    def _load_resume_summary(self, agent_name: str) -> str:
        """Load an explicit checkpoint into prompt memory and trace."""

        resume_state = getattr(self.config, "resume_state", "")
        if not resume_state:
            return ""
        checkpoint = TaskStateStore.load_path(resume_state)
        summary = summarize_checkpoint(checkpoint)
        self.trace.add(
            0,
            agent_name,
            "resume_state_loaded",
            resume_state=resume_state,
            checkpoint=checkpoint.to_dict(),
            resume_summary=summary,
        )
        return summary
