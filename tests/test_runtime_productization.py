import json
import tempfile
import threading
import unittest
from pathlib import Path

from agent_forge import (
    Harness,
    HarnessConfig,
    HarnessExtensions,
    ModelCapabilities,
    RunController,
    RuntimeHook,
    TaskRunStatus,
)
from agent_forge.extensions import (
    AgentResponse,
    EventStreamPolicy,
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


class FinalModel:
    last_usage = None

    def chat(self, messages, tools):
        return AgentResponse("original final", [])


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
        return HookDecision(self.name, HookDecisionType.DENY, "verification missing")


class RuntimeProductizationTest(unittest.TestCase):
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
            worker = threading.Thread(target=lambda: outcome.append(harness.run("old task")))
            worker.start()
            self.assertTrue(model.entered.wait(timeout=3))
            controller.steer("new operator direction")
            model.release.set()
            worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            result = outcome[0]
            self.assertEqual(result.status, TaskRunStatus.COMPLETED)
            self.assertTrue(result.final_answer.startswith("steer applied"))
            self.assertIn(
                "new operator direction",
                "\n".join(message.content for message in model.messages),
            )
            names = [event.name for event in collector.events]
            self.assertEqual(names[0], "run.started")
            self.assertIn("run.started", names)
            self.assertIn("run.control", names)
            self.assertIn("checkpoint.saved", names)
            self.assertIn("run.completed", names)
            control_event = next(event for event in collector.events if event.name == "run.control")
            self.assertNotIn("new operator direction", json.dumps(control_event.to_dict()))

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
            worker = threading.Thread(target=lambda: outcome.append(harness.run("cancel me")))
            worker.start()
            self.assertTrue(model.entered.wait(timeout=3))
            controller.cancel("operator changed priorities")
            model.release.set()
            worker.join(timeout=5)

            result = outcome[0]
            self.assertEqual(result.status, TaskRunStatus.CANCELLED)
            self.assertEqual(result.stop_reason, "cancel")
            self.assertIn("already completed side effects", result.checkpoint.resume_hint)

    def test_pause_persists_a_resumable_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = RunController()
            controller.pause("inspect before continuing")
            harness = Harness(
                model=FinalModel(),
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
                model=FinalModel(),
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
                item["hook_name"]
                for item in before_model["hook_result"]["decisions"]
            }
            self.assertIn("permission_policy", hook_names)
            self.assertIn("rewrite_hook", hook_names)

            gated = Harness(
                model=FinalModel(),
                tools=ToolRegistry(),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs-b"),
                    max_steps=2,
                ),
                extensions=HarnessExtensions(
                    lifecycle_hooks=(RejectCompletionHook(),)
                ),
            ).run("claim completion")
            self.assertEqual(gated.status, TaskRunStatus.BLOCKED)
            self.assertEqual(gated.stop_reason, "stop_hook_blocked")

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
            self.assertEqual(context_window["context_window"]["hard_input_limit"], 1_536)

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


if __name__ == "__main__":
    unittest.main()
