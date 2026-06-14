"""Runtime-backed supervised multi-agent orchestration.

The initial implementation used direct role-function calls. That was useful for
understanding the phase story, but weak as an orchestration design. This version
keeps the readable phase story while adding production-oriented structure:

* every subagent is described by an ``AgentSpec``;
* each worker runs through ``AgentRuntime`` and therefore through AgentLoop;
* dependencies are represented as a small task graph;
* failed tests trigger a retry node instead of being hidden in if/else prose;
* handoff and scheduler events are still visible in trace.

The scheduler supports conflict-aware parallel batches. This demo task is still
mostly dependency-bound, but the orchestration layer has the production
contracts system reviewers expect: runtime-backed workers, file ownership,
artifacts, retry, and review gates.
"""

from agent_forge.models.gateway import ModelGateway
from agent_forge.production.ownership import OwnershipPlan
from agent_forge.runtime.agent_runtime import AgentRuntime
from agent_forge.runtime.agent_spec import AgentRunResult, AgentSpec
from agent_forge.runtime.llm_client import MockLLMClient
from agent_forge.workflows.task_graph import TaskGraph, TaskNode, TaskScheduler

from .handoff import Handoff
from .supervisor_phase import TaskPhase
from .supervisor_policy import SupervisorPolicy


class SupervisorAgent:
    """Coordinate runtime-backed planner/coder/tester/reviewer workers.

    This is a supervised multi-agent design. The supervisor owns graph,
    ownership, retry, and review policy; subagents only execute their bounded
    role through AgentRuntime.
    """

    def __init__(self, policy: SupervisorPolicy | None = None, workspace: str = "."):
        """Keep retry policy injectable for tests and technical walkthroughs."""

        self.policy = policy or SupervisorPolicy(max_retry=1)
        self.workspace = workspace

    def _payload(self, phase: TaskPhase, state: dict) -> dict:
        """Build the auditable handoff payload written into trace events.

        Handoff payloads are the answer to "how do agents communicate?" in this
        project: structured state and evidence, not free-form hidden chat.
        """

        return {
            "phase": phase.value,
            "task": state.get("task", ""),
            "relevant_files": state.get(
                "relevant_files",
                ["examples/demo_repo/src/calculator.py", "examples/demo_repo/tests/test_calculator.py"],
            ),
            "modified_files": state.get("modified_files", []),
            "test_result": state.get("test_result"),
            "review_result": state.get("review"),
            "retry_count": state.get("retry_count", 0),
        }

    def _handoff(self, trace, step: int, to_agent: str, reason: str, phase: TaskPhase, state: dict):
        """Record one supervisor-to-subagent transition before work starts."""

        handoff = Handoff("SupervisorAgent", to_agent, reason, self._payload(phase, state))
        trace.add(
            step,
            "SupervisorAgent",
            "handoff",
            from_agent=handoff.from_agent,
            to_agent=handoff.to_agent,
            reason=handoff.reason,
            handoff=handoff.__dict__,
        )
        return handoff

    def _specs(self) -> dict[str, AgentSpec]:
        """Define role prompts and tool allowlists in one obvious place.

        Keeping specs centralized makes role boundaries auditable. If a
        ReviewerAgent ever edits files, that would be visible here immediately.
        """

        return {
            "PlannerAgent": AgentSpec(
                name="PlannerAgent",
                role="planner",
                system_prompt="You are the planning worker. Produce a small implementation plan.",
                allowed_tools=set(),
                max_steps=2,
            ),
            "CodingAgent": AgentSpec(
                # Coder is the only role that can patch the source file in this
                # demo, so it receives write ownership and a medium risk label.
                name="CodingAgent",
                role="coder",
                system_prompt="You are the coding worker. Read files and apply the required patch.",
                allowed_tools={"read_file", "apply_patch"},
                max_steps=4,
                read_files={"examples/demo_repo/src/calculator.py"},
                write_files={"examples/demo_repo/src/calculator.py"},
                risk_level="medium",
            ),
            "TesterAgent": AgentSpec(
                # Tester gets command execution but no patch tool. This prevents
                # validation workers from silently changing the code they test.
                name="TesterAgent",
                role="tester",
                system_prompt="You are the validation worker. Run the relevant tests and report evidence.",
                allowed_tools={"run_command"},
                max_steps=3,
                read_files={"examples/demo_repo/tests/test_calculator.py"},
            ),
            "ReviewerAgent": AgentSpec(
                # Reviewer inspects git evidence only. In production this role
                # would also verify risk summaries and policy compliance.
                name="ReviewerAgent",
                role="reviewer",
                system_prompt="You are the review worker. Inspect diff evidence and summarize risk.",
                allowed_tools={"git_diff", "git_status"},
                max_steps=3,
                read_files={"examples/demo_repo/src/calculator.py", "examples/demo_repo/tests/test_calculator.py"},
            ),
        }

    def _runtime(self, spec: AgentSpec, registry, trace, llm_mode: str) -> AgentRuntime:
        """Create one runtime-backed worker with a role-specific mock model."""

        gateway = ModelGateway(
            primary=MockLLMClient(llm_mode),
            provider="mock",
            model=f"mock-{llm_mode}",
            retry_policy=None,
        )
        return AgentRuntime(spec, self.workspace, registry, trace, gateway)

    def _last_tool_success(self, result: AgentRunResult, tool_name: str) -> bool:
        """Read tool success from a worker trace slice.

        The supervisor should validate workers using trace evidence, not just
        the natural-language final answer of a subagent.
        """

        for event in reversed(result.events):
            if event.get("event_type") == "tool_observation" and event.get("tool_call") == tool_name:
                return bool(event.get("success", False))
        for event in reversed(result.events):
            if event.get("event_type") == "tool_observation":
                return bool(event.get("success", False))
        return result.success

    def run(self, trace, task: str, registry):
        """Run a runtime-backed supervised graph with one test-driven retry."""

        trace.set_run_context(task=task)
        specs = self._specs()

        # Shared supervisor state is intentionally small and explicit. It is the
        # project's equivalent of a production workflow state row.
        state = {"task": task, "trace": trace, "registry": registry, "retry_count": 0}
        ownership = OwnershipPlan()
        for spec in specs.values():
            ownership.claim(spec.name, spec.write_files)
        trace.add(
            0,
            "SupervisorAgent",
            "ownership_plan",
            ownership=ownership.to_dict(),
            success=not ownership.has_conflicts(),
        )
        lines = []
        step = 1

        graph = TaskGraph()

        # First graph: plan -> code -> test. It is dependency-bound by design;
        # the scheduler still supports parallel conflict-safe batches for larger
        # graphs.
        graph.add(TaskNode("plan", "PlannerAgent", task))
        graph.add(
            TaskNode(
                "code",
                "CodingAgent",
                task,
                depends_on=["plan"],
                read_files=set(specs["CodingAgent"].read_files),
                write_files=set(specs["CodingAgent"].write_files),
                expected_artifacts={"agent_result"},
            )
        )
        graph.add(
            TaskNode(
                "test",
                "TesterAgent",
                task,
                depends_on=["code"],
                read_files=set(specs["TesterAgent"].read_files),
                expected_artifacts={"agent_result"},
            )
        )

        llm_modes = {
            # Mock modes simulate role-specific model behavior while still
            # exercising the real AgentLoop/tool/trace path.
            "plan": "planner",
            "code": "coder_fail",
            "test": "tester",
        }

        def execute(node: TaskNode) -> AgentRunResult:
            """Run one task node and fold its result into supervisor state."""

            nonlocal step
            phase = {
                "PlannerAgent": TaskPhase.PLANNING,
                "CodingAgent": TaskPhase.CODING,
                "TesterAgent": TaskPhase.TESTING,
                "ReviewerAgent": TaskPhase.REVIEWING,
            }[node.agent_name]
            self._handoff(trace, step, node.agent_name, node.node_id, phase, state)
            label = node.agent_name
            if node.node_id == "code_retry":
                label = "CodingAgent (retry)"
            elif node.node_id == "test_retry":
                label = "TesterAgent (retest)"
            lines.append(f"SupervisorAgent -> {label}")
            step += 1
            result = self._runtime(specs[node.agent_name], registry, trace, llm_modes[node.node_id]).run(node.task)

            # Store final answer and artifacts separately. Prose is useful for
            # humans; artifacts/counters are what supervisor policy should read.
            state[node.agent_name] = result.final_answer
            state.setdefault("artifacts", []).extend(artifact.to_dict() for artifact in result.artifacts)
            if node.agent_name == "CodingAgent":
                state["code_done"] = result.success
                if self._last_tool_success(result, "apply_patch"):
                    state["modified_files"] = ["examples/demo_repo/src/calculator.py"]
            if node.agent_name == "TesterAgent":
                state["test_pass"] = self._last_tool_success(result, "run_command")
                state["test_result"] = result.final_answer
            if node.agent_name == "ReviewerAgent":
                state["review"] = "approved" if state.get("test_pass") else "changes required"
            return result

        TaskScheduler(graph, {name: execute for name in specs}, max_workers=4).run()

        if not state.get("test_pass") and state.get("retry_count", 0) < self.policy.max_retry:
            # Retry is triggered by test evidence, not by the coder claiming it
            # succeeded. This is the core "supervisor validates subagents" point.
            state["retry_count"] = 1
            retry_graph = TaskGraph()
            retry_graph.add(
                TaskNode(
                    "code_retry",
                    "CodingAgent",
                    task,
                    read_files=set(specs["CodingAgent"].read_files),
                    write_files=set(specs["CodingAgent"].write_files),
                    expected_artifacts={"agent_result"},
                )
            )
            retry_graph.add(
                TaskNode(
                    "test_retry",
                    "TesterAgent",
                    task,
                    depends_on=["code_retry"],
                    read_files=set(specs["TesterAgent"].read_files),
                    expected_artifacts={"agent_result"},
                )
            )
            llm_modes.update({"code_retry": "coder_fix", "test_retry": "tester"})
            TaskScheduler(retry_graph, {name: execute for name in specs}, max_workers=4).run()

        review_graph = TaskGraph()

        # Review always runs last as a gate over diff/test evidence.
        review_graph.add(
            TaskNode(
                "review",
                "ReviewerAgent",
                task,
                read_files=set(specs["ReviewerAgent"].read_files),
                expected_artifacts={"agent_result"},
            )
        )
        llm_modes["review"] = "reviewer"
        TaskScheduler(review_graph, {name: execute for name in specs}, max_workers=4).run()

        final_phase = TaskPhase.DONE if state.get("test_pass") and state.get("review") == "approved" else TaskPhase.FAILED
        final = "pass" if final_phase == TaskPhase.DONE else "fail"
        output = "\n".join(lines + [f"Final: {final}; review={state.get('review', '')}; retry={state.get('retry_count',0)}"])
        trace.set_run_context(stop_reason=final_phase.value, final_answer=output)
        return output
