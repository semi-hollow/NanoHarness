import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agent_forge import (
    Harness,
    HarnessConfig,
    HarnessExtensions,
    ModelCapabilities,
    RunController,
    RunRequest,
    RuntimeHook,
    TaskRunStatus,
)
from agent_forge.extensions import (
    AgentResponse,
    HookDecision,
    HookDecisionType,
    Observation,
    RuntimeEvent,
    Tool,
    ToolArguments,
    ToolCall,
    ToolRegistry,
    ToolSchema,
)
from agent_forge.observability.api import TraceRecorder
from agent_forge.runtime.application.model_step_preparation import PreparedModelStep
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.domain.run_control import RuntimeCoordinationSignal
from agent_forge.runtime.domain.thread import (
    ConversationItemDraft,
    ConversationThread,
    ThreadRun,
    Turn,
)
from agent_forge.runtime.adapters import (
    JsonConversationThreadRepository,
    JsonTaskStateRepository,
)
from agent_forge.runtime.domain.task import TaskStartRequest
from agent_forge.runtime.wiring import (
    AgentLoopBuildRequest,
    RuntimeDependencyOverrides,
    build_agent_loop_from_request,
)
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.builtins.replace_text import ReplaceTextTool
from tests.support import StaticResponseModel


class EventCollector:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    def on_event(self, event: RuntimeEvent) -> None:
        self.events.append(event)


class BlockingSteerModel:
    last_usage = None

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.messages = []

    def chat(self, messages, tools):
        self.calls += 1
        self.messages = list(messages)
        if self.calls == 1:
            self.entered.set()
            if not self.release.wait(timeout=3):
                raise TimeoutError("test did not release the model")
            return AgentResponse("stale answer that must be discarded", [])
        return AgentResponse("steer applied", [])


class MutateInstructionThenFinishModel:
    """修改 governing file 后记录同一 Turn 下一 Model Step 的 System 输入。"""

    last_usage = None

    def __init__(self) -> None:
        self.system_inputs: list[str] = []

    def chat(self, messages, tools):
        self.system_inputs.append(messages[0].content)
        if len(self.system_inputs) == 1:
            return AgentResponse(
                "",
                [
                    ToolCall(
                        "change-instructions",
                        "replace_text",
                        {
                            "path": "AGENTS.md",
                            "old": "governing-rule-v1",
                            "new": "governing-rule-v2",
                        },
                    )
                ],
            )
        return AgentResponse("instruction mutation inspected", [])


class CaptureInstructionModel:
    last_usage = None

    def __init__(self) -> None:
        self.system_input = ""

    def chat(self, messages, tools):
        self.system_input = messages[0].content
        return AgentResponse("new turn inspected current instructions", [])


class OverflowThenBlockingToolModel:
    """首次溢出，recovery 返回一个必须被丢弃的 ToolCall。"""

    last_usage = None

    def __init__(self) -> None:
        self.recovery_entered = threading.Event()
        self.release_recovery = threading.Event()
        self.calls = 0
        self.messages = []

    def chat(self, messages, tools):
        self.calls += 1
        self.messages = list(messages)
        if self.calls == 1:
            return AgentResponse(
                None,
                [],
                error={
                    "code": "context_length_exceeded",
                    "message": "maximum context length exceeded",
                },
            )
        if self.calls == 2:
            self.recovery_entered.set()
            if not self.release_recovery.wait(timeout=3):
                raise TimeoutError("test did not release overflow recovery model")
            return AgentResponse(
                None,
                [ToolCall("stale-call", "counting_tool", {})],
            )
        return AgentResponse("fresh response after new input", [])


class ScriptedOverflowModelStepPreparation:
    """只固定 overflow recovery 前后的输入规模，不伪造模型结果。"""

    def __init__(self, tool: Tool) -> None:
        self.tool = tool

    def prepare_model_step(self, session, step, *, force_compaction=False):
        system_message = Message("system", "runtime policy")
        return PreparedModelStep(
            step=step,
            model_step_system_message=system_message,
            llm_messages=[system_message, *session.messages],
            tool_schemas=[self.tool.schema()],
            allowed_tool_names={self.tool.name},
            history_chars=sum(len(message.content) for message in session.messages),
            tool_schema_chars=len(str(self.tool.schema())),
            estimated_prompt_tokens=400 if force_compaction else 900,
            compacted=force_compaction,
            conversation_history_digest=None,
            phase="execute",
        )


class CoordinationController(RunController):
    """测试用 controller：在真实 after_model 边界交付 Runtime coordination。"""

    def __init__(self) -> None:
        super().__init__()
        self._coordination_lock = threading.Lock()
        self._coordination = []

    def publish_coordination(self) -> None:
        signal = RuntimeCoordinationSignal(
            event_id="feedback-1",
            content="upstream contract changed",
            plan_digest="a" * 64,
            worker_attempt_id=1,
            publisher_task_id="worker-b",
            target_task_id="worker-a",
            event_type="FEEDBACK",
            semantic_key="api-contract",
            version=1,
        )
        with self._coordination_lock:
            self._coordination.append(signal)

    def drain_coordination(self, run_id, *, boundary):
        with self._coordination_lock:
            signals = list(self._coordination)
            self._coordination.clear()
        return signals


class CaptureContextModel:
    last_usage = None

    def __init__(self):
        self.messages = []

    def chat(self, messages, tools):
        self.messages = list(messages)
        return AgentResponse("context captured", [])


class CountingTool(Tool):
    description = "count one governed execution"

    def __init__(self, name):
        self.name = name
        self.calls = 0

    def schema(self) -> ToolSchema:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": {},
            "required": [],
        }

    def execute(self, arguments: ToolArguments) -> Observation:
        self.calls += 1
        return Observation(self.name, True, "done")


class CapabilityModel:
    last_usage = None
    capabilities = ModelCapabilities(
        parallel_tool_calls=False,
        context_window=2_048,
        source="test declaration",
    )

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                None,
                [ToolCall("one", "tool_one", {}), ToolCall("two", "tool_two", {})],
            )
        return AgentResponse("capability policy applied", [])


class RewriteHook(RuntimeHook):
    name = "rewrite_hook"

    def __init__(self) -> None:
        self.checkpoints = []

    def after_model(self, context, response):
        return AgentResponse("normalized final", response.tool_calls)

    def on_checkpoint(self, checkpoint):
        self.checkpoints.append(checkpoint.status)


class RejectCompletionHook(RuntimeHook):
    name = "quality_gate"

    def on_stop(self, run_id, reason, final_answer):
        return HookDecision(
            hook_name=self.name,
            decision=HookDecisionType.DENY,
            reason="verification missing",
        )


class RuntimeProductizationTest(unittest.TestCase):
    def test_explicit_test_pass_claim_without_validation_is_not_completed(self):
        """窄规则命中时保留 candidate，但不生成 accepted final answer。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = Harness(
                model=StaticResponseModel("All tests passed; the repair is complete."),
                tools=ToolRegistry(),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    max_steps=2,
                ),
            ).run("repair without running validation")

            self.assertEqual(result.status, TaskRunStatus.BLOCKED)
            self.assertEqual(result.stop_reason, "unsupported_validation_claim")
            self.assertIsNone(result.final_answer)
            self.assertFalse((result.artifact_dir / "final_answer.txt").exists())
            trace = json.loads(result.trace_path.read_text(encoding="utf-8"))
            self.assertIsNone(trace["final_answer"])
            self.assertTrue(
                any(
                    event["event_type"] == "candidate_final_answer"
                    and "All tests passed" in event.get("observation", "")
                    for event in trace["events"]
                )
            )

    def test_final_answer_does_not_append_a_hardcoded_online_load_test_disclaimer(self):
        """未评估的声明由模型与具体任务决定，Runtime 不伪造固定项。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = Harness(
                model=StaticResponseModel("Implementation complete; validation not run."),
                tools=ToolRegistry(),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    max_steps=2,
                ),
            ).run("explain the implementation")

            self.assertEqual(result.status, TaskRunStatus.COMPLETED)
            self.assertEqual(
                result.final_answer,
                "Implementation complete; validation not run.",
            )
            self.assertNotIn("线上压测", result.stop_output)

    def test_overflow_recovery_response_rechecks_every_after_model_signal(self):
        """recovery provider call 期间到达的信号必须阻止旧 ToolCall。"""

        for signal_kind in ("steer", "coordination", "cancel"):
            with self.subTest(signal=signal_kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                controller = CoordinationController()
                model = OverflowThenBlockingToolModel()
                tool = CountingTool("counting_tool")
                registry = ToolRegistry()
                registry.register(tool)
                trace = TraceRecorder(str(root / "trace.json"))
                thread_id = "thread-overflow"
                turn_id = "turn-overflow"
                thread_repository = JsonConversationThreadRepository(root / "threads")
                now = time.time()
                thread_repository.create(
                    ConversationThread(
                        thread_id=thread_id,
                        title="overflow recovery",
                        initial_task="repair target",
                        workspace=str(root),
                        created_at=now,
                        updated_at=now,
                    )
                )
                JsonTaskStateRepository(root / "task-state").start(
                    TaskStartRequest(
                        run_id=trace.run_id,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        workspace=str(root),
                        execution_workspace=str(root),
                        execution_mode="local",
                        agent_name="CodingAgent",
                    )
                )
                thread_repository.start_turn(
                    thread_id,
                    Turn(
                        turn_id=turn_id,
                        root_task="repair target",
                        input_item_id=f"user:{turn_id}",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    ConversationItemDraft(
                        item_id=f"user:{turn_id}",
                        turn_id=turn_id,
                        run_id=trace.run_id,
                        role="user",
                        content="repair target",
                        origin="human",
                        human_authority=True,
                    ),
                    ThreadRun(
                        run_id=trace.run_id,
                        artifact_dir=str(root / "artifacts" / trace.run_id),
                        checkpoint_path=str(
                            root / "task-state" / f"{trace.run_id}.json"
                        ),
                        status="created",
                        relationship="initial",
                        created_at=now,
                        updated_at=now,
                    ),
                )
                config = RuntimeConfig(
                    workspace=str(root),
                    requested_workspace=str(root),
                    thread_id=thread_id,
                    turn_id=turn_id,
                    conversation_thread_root=str(root / "threads"),
                    max_steps=3,
                    trace_file=str(root / "trace.json"),
                    task_state_root=str(root / "task-state"),
                    approval_root=str(root / "approvals"),
                    human_input_root=str(root / "human-input"),
                    operation_ledger_root=str(root / "ledger"),
                    memory_root=str(root / "memory"),
                )
                loop = build_agent_loop_from_request(
                    AgentLoopBuildRequest(
                        config=config,
                        trace=trace,
                        registry=registry,
                        llm=model,
                        overrides=RuntimeDependencyOverrides(
                            control=controller,
                            conversation_threads=thread_repository,
                        ),
                    )
                )
                loop.model_step_preparation = ScriptedOverflowModelStepPreparation(tool)

                outcome = []
                worker = threading.Thread(
                    target=lambda: outcome.append(loop.run())
                )
                worker.start()
                self.assertTrue(model.recovery_entered.wait(timeout=3))
                if signal_kind == "steer":
                    controller.steer("use the newly supplied constraint")
                elif signal_kind == "coordination":
                    controller.publish_coordination()
                else:
                    controller.cancel("operator cancelled during recovery")
                model.release_recovery.set()
                worker.join(timeout=5)

                self.assertFalse(worker.is_alive())
                self.assertEqual(tool.calls, 0)
                self.assertFalse(
                    any(
                        event["event_type"] == "tool_execution_started"
                        for event in trace.events
                    )
                )
                if signal_kind == "cancel":
                    self.assertIn("cancel", outcome[0])
                    self.assertEqual(model.calls, 2)
                else:
                    self.assertIn("fresh response after new input", outcome[0])
                    self.assertEqual(model.calls, 3)
                    self.assertTrue(
                        any(
                            event["event_type"] == "recovery_decision"
                            and event.get("failure_kind")
                            in {"operator_steer", "runtime_coordination"}
                            and event.get("model_step_outcome") == "refresh_input"
                            for event in trace.events
                        )
                    )

    def test_steer_discards_in_flight_model_result_and_streams_safe_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = RunController()
            collector = EventCollector()
            model = BlockingSteerModel()
            harness = Harness(
                model=model,
                tools=ToolRegistry(),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    max_steps=3,
                ),
                extensions=HarnessExtensions(
                    run_control=controller,
                    event_listeners=(collector,),
                ),
            )
            outcome = []
            worker = threading.Thread(
                target=lambda: outcome.append(harness.run("old task"))
            )
            worker.start()
            self.assertTrue(model.entered.wait(timeout=3))
            controller.steer("first operator direction")
            controller.steer("second operator detail")
            model.release.set()
            worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            result = outcome[0]
            self.assertEqual(result.status, TaskRunStatus.COMPLETED)
            self.assertTrue(result.final_answer.startswith("steer applied"))
            model_context = "\n".join(message.content for message in model.messages)
            self.assertLess(
                model_context.index("first operator direction"),
                model_context.index("second operator detail"),
            )
            self.assertEqual(model.calls, 2)
            names = [event.name for event in collector.events]
            self.assertEqual(names[0], "run.started")
            self.assertIn("run.started", names)
            self.assertIn("run.control", names)
            self.assertIn("checkpoint.saved", names)
            self.assertIn("run.completed", names)
            control_event = next(
                event for event in collector.events if event.name == "run.control"
            )
            serialized_event = json.dumps(control_event.to_dict())
            self.assertNotIn("first operator direction", serialized_event)
            self.assertNotIn("second operator detail", serialized_event)

    def test_cancel_is_cooperative_and_stops_before_processing_model_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = RunController()
            model = BlockingSteerModel()
            harness = Harness(
                model=model,
                tools=ToolRegistry(),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    max_steps=2,
                ),
                extensions=HarnessExtensions(run_control=controller),
            )
            outcome = []
            worker = threading.Thread(
                target=lambda: outcome.append(harness.run("cancel me"))
            )
            worker.start()
            self.assertTrue(model.entered.wait(timeout=3))
            controller.cancel("operator changed priorities")
            model.release.set()
            worker.join(timeout=5)

            result = outcome[0]
            self.assertEqual(result.status, TaskRunStatus.CANCELLED)
            self.assertEqual(result.stop_reason, "cancel")
            self.assertIn(
                "already completed side effects", result.checkpoint.resume_hint
            )

    def test_pause_persists_a_resumable_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = RunController()
            controller.pause("inspect before continuing")
            harness = Harness(
                model=StaticResponseModel("original final"),
                tools=ToolRegistry(),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    max_steps=2,
                ),
                extensions=HarnessExtensions(run_control=controller),
            )

            paused = harness.run("pause this task")

            self.assertEqual(paused.status, TaskRunStatus.PAUSED)
            self.assertTrue(paused.waiting_for_operator)
            checkpoint_path = (
                paused.artifact_dir / "task_state" / f"{paused.run_id}.json"
            )
            resumed = harness.resume(checkpoint_path)
            self.assertEqual(resumed.status, TaskRunStatus.COMPLETED)

    def test_lifecycle_hook_composes_with_safety_and_can_gate_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rewrite = RewriteHook()
            harness = Harness(
                model=StaticResponseModel("original final"),
                tools=ToolRegistry(),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs-a"),
                    max_steps=2,
                ),
                extensions=HarnessExtensions(lifecycle_hooks=(rewrite,)),
            )
            rewritten = harness.run("finish normally")
            self.assertTrue(rewritten.final_answer.startswith("normalized final"))
            self.assertGreaterEqual(len(rewrite.checkpoints), 2)
            trace = json.loads(rewritten.trace_path.read_text(encoding="utf-8"))
            before_model = next(
                event
                for event in trace["events"]
                if event["event_type"] == "hook_check"
                and event.get("hook_stage") == "before_model"
            )
            hook_names = {
                item["hook_name"] for item in before_model["hook_result"]["decisions"]
            }
            self.assertIn("permission_policy", hook_names)
            self.assertIn("rewrite_hook", hook_names)

            gated = Harness(
                model=StaticResponseModel("original final"),
                tools=ToolRegistry(),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs-b"),
                    max_steps=2,
                ),
                extensions=HarnessExtensions(lifecycle_hooks=(RejectCompletionHook(),)),
            ).run("claim completion")
            self.assertEqual(gated.status, TaskRunStatus.BLOCKED)
            self.assertEqual(gated.stop_reason, "stop_hook_blocked")
            self.assertIsNone(gated.final_answer)
            self.assertEqual(
                (gated.artifact_dir / "stop_output.txt").read_text(encoding="utf-8"),
                gated.stop_output,
            )
            self.assertFalse((gated.artifact_dir / "final_answer.txt").exists())
            gated_trace = json.loads(gated.trace_path.read_text(encoding="utf-8"))
            self.assertIsNone(gated_trace["final_answer"])
            self.assertTrue(
                any(
                    event["event_type"] == "candidate_final_answer"
                    for event in gated_trace["events"]
                )
            )

    def test_model_capabilities_bound_context_and_parallel_tool_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = CountingTool("tool_one")
            second = CountingTool("tool_two")
            registry = ToolRegistry()
            registry.register(first)
            registry.register(second)
            result = Harness(
                model=CapabilityModel(),
                tools=registry,
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    max_steps=3,
                    max_prompt_tokens=8_192,
                    reserved_output_tokens=512,
                    tool_routing_mode="all",
                ),
            ).run("execute the provided tools")

            self.assertEqual(result.status, TaskRunStatus.COMPLETED)
            self.assertEqual(first.calls, 1)
            self.assertEqual(second.calls, 0)
            trace = json.loads(result.trace_path.read_text(encoding="utf-8"))
            capability_event = next(
                event
                for event in trace["events"]
                if event["event_type"] == "model_capabilities"
            )
            self.assertEqual(
                capability_event["model_capabilities"]["context_window"],
                2_048,
            )
            context_window = next(
                event
                for event in trace["events"]
                if event["event_type"] == "context_window"
            )
            self.assertEqual(
                context_window["context_window"]["hard_input_limit"], 1_536
            )
            before_tool = next(
                event
                for event in trace["events"]
                if event["event_type"] == "hook_check"
                and event.get("tool_call") == "tool_one"
            )
            self.assertEqual(before_tool["hook_stage"], "before_tool")

    def test_instruction_hierarchy_reaches_the_real_model_context_and_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "AGENTS.md").write_text("root instruction", encoding="utf-8")
            (root / "src" / "FORGE.local.md").write_text(
                "directory override", encoding="utf-8"
            )
            model = CaptureContextModel()
            result = Harness(
                model=model,
                tools=ToolRegistry(),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    instruction_target="src",
                    runtime_instructions="runtime override",
                    max_steps=2,
                ),
            ).run("inspect instructions")

            model_input = "\n".join(message.content for message in model.messages)
            self.assertIn("root instruction", model_input)
            self.assertIn("directory override", model_input)
            self.assertIn("runtime override", model_input)
            trace = json.loads(result.trace_path.read_text(encoding="utf-8"))
            context_event = next(
                event
                for event in trace["events"]
                if event["event_type"] == "context_assembly"
            )
            sources = context_event["context"]["instructions"]["sources"]
            self.assertEqual(
                [source["kind"] for source in sources],
                ["repository", "local_override", "runtime_override"],
            )
            request_artifact = json.loads(
                (result.artifact_dir / "run_request.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("runtime override", json.dumps(request_artifact))

    def test_turn_snapshot_ignores_mid_turn_instruction_mutation_but_new_turn_refreshes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text(
                "governing-rule-v1",
                encoding="utf-8",
            )
            registry = ToolRegistry()
            registry.register(ReplaceTextTool(WorkspaceSandbox(root)))
            config = HarnessConfig(
                workspace=str(root),
                output_root=str(root / "runs"),
                instruction_target=".",
                max_steps=3,
                skill_mode="none",
                tool_routing_mode="all",
            )
            first_model = MutateInstructionThenFinishModel()
            first = Harness(
                model=first_model,
                tools=registry,
                config=config,
            ).run("Change the instruction file once, then report the current policy.")

            self.assertEqual(first.status, TaskRunStatus.COMPLETED)
            self.assertEqual(len(first_model.system_inputs), 2)
            self.assertIn("governing-rule-v1", first_model.system_inputs[0])
            self.assertIn("governing-rule-v1", first_model.system_inputs[1])
            self.assertNotIn("governing-rule-v2", first_model.system_inputs[1])
            self.assertEqual(
                (root / "AGENTS.md").read_text(encoding="utf-8"),
                "governing-rule-v2",
            )

            second_model = CaptureInstructionModel()
            second = Harness(
                model=second_model,
                tools=registry,
                config=config,
            ).run(
                RunRequest(
                    "Read the governing policy for this new request.",
                    thread_id=first.thread_id,
                )
            )
            self.assertEqual(second.status, TaskRunStatus.COMPLETED)
            self.assertIn("governing-rule-v2", second_model.system_input)
            self.assertNotIn("governing-rule-v1", second_model.system_input)


if __name__ == "__main__":
    unittest.main()
