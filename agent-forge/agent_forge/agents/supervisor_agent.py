"""Runtime-backed supervised multi-agent orchestration.

The original MVP used direct ``PlannerAgent().run(state)`` calls. That was
useful for teaching, but too toy-like for senior AI agent interviews. This
version keeps the same readable phase story while adding production-oriented
structure:

* every subagent is described by an ``AgentSpec``;
* each worker runs through ``AgentRuntime`` and therefore through AgentLoop;
* dependencies are represented as a small task graph;
* failed tests trigger a retry node instead of being hidden in if/else prose;
* handoff and scheduler events are still visible in trace.

The scheduler remains sequential for determinism. The important upgrade is the
architecture: the supervisor now coordinates runtime-backed workers, which is
the shape that can evolve into parallel DAG execution.
"""

from agent_forge.models.gateway import ModelGateway
from agent_forge.runtime.agent_runtime import AgentRuntime
from agent_forge.runtime.agent_spec import AgentRunResult, AgentSpec
from agent_forge.runtime.llm_client import MockLLMClient
from agent_forge.workflows.task_graph import TaskGraph, TaskNode, TaskScheduler, TaskStatus

from .handoff import Handoff
from .supervisor_phase import TaskPhase
from .supervisor_policy import SupervisorPolicy


class SupervisorAgent:
    """Coordinate runtime-backed planner/coder/tester/reviewer workers."""

    def __init__(self, policy: SupervisorPolicy | None = None, workspace: str = "."):
        """Keep retry policy injectable for tests and interviews."""

        self.policy = policy or SupervisorPolicy(max_retry=1)
        self.workspace = workspace

    def _payload(self, phase: TaskPhase, state: dict) -> dict:
        """Build the auditable handoff payload written into trace events."""

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
        """Define role prompts and tool allowlists in one obvious place."""

        return {
            "PlannerAgent": AgentSpec(
                name="PlannerAgent",
                role="planner",
                system_prompt="You are the planning worker. Produce a small implementation plan.",
                allowed_tools=set(),
                max_steps=2,
            ),
            "CodingAgent": AgentSpec(
                name="CodingAgent",
                role="coder",
                system_prompt="You are the coding worker. Read files and apply the required patch.",
                allowed_tools={"read_file", "apply_patch"},
                max_steps=4,
            ),
            "TesterAgent": AgentSpec(
                name="TesterAgent",
                role="tester",
                system_prompt="You are the validation worker. Run the relevant tests and report evidence.",
                allowed_tools={"run_command"},
                max_steps=3,
            ),
            "ReviewerAgent": AgentSpec(
                name="ReviewerAgent",
                role="reviewer",
                system_prompt="You are the review worker. Inspect diff evidence and summarize risk.",
                allowed_tools={"git_diff", "git_status"},
                max_steps=3,
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
        """Read tool success from a worker trace slice."""

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
        state = {"task": task, "trace": trace, "registry": registry, "retry_count": 0}
        lines = []
        step = 1

        graph = TaskGraph()
        graph.add(TaskNode("plan", "PlannerAgent", task))
        graph.add(TaskNode("code", "CodingAgent", task, depends_on=["plan"]))
        graph.add(TaskNode("test", "TesterAgent", task, depends_on=["code"]))

        llm_modes = {
            "plan": "planner",
            "code": "coder_fail",
            "test": "tester",
        }

        def execute(node: TaskNode) -> AgentRunResult:
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
            state[node.agent_name] = result.final_answer
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

        TaskScheduler(graph, {name: execute for name in specs}).run()

        if not state.get("test_pass") and state.get("retry_count", 0) < self.policy.max_retry:
            state["retry_count"] = 1
            retry_graph = TaskGraph()
            retry_graph.add(TaskNode("code_retry", "CodingAgent", task))
            retry_graph.add(TaskNode("test_retry", "TesterAgent", task, depends_on=["code_retry"]))
            llm_modes.update({"code_retry": "coder_fix", "test_retry": "tester"})
            TaskScheduler(retry_graph, {name: execute for name in specs}).run()

        review_graph = TaskGraph()
        review_graph.add(TaskNode("review", "ReviewerAgent", task))
        llm_modes["review"] = "reviewer"
        TaskScheduler(review_graph, {name: execute for name in specs}).run()

        final_phase = TaskPhase.DONE if state.get("test_pass") and state.get("review") == "approved" else TaskPhase.FAILED
        final = "pass" if final_phase == TaskPhase.DONE else "fail"
        output = "\n".join(lines + [f"Final: {final}; review={state.get('review', '')}; retry={state.get('retry_count',0)}"])
        trace.set_run_context(stop_reason=final_phase.value, final_answer=output)
        return output
