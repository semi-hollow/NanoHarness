import ast
import tempfile
import unittest
from pathlib import Path

from agent_forge.observability.api import TraceRecorder
from agent_forge.runtime.adapters import JsonTaskStateRepository


class TypeContractTest(unittest.TestCase):
    def test_every_production_function_has_an_explicit_signature(self) -> None:
        """Keep local code navigation useful even when mypy is not installed."""

        missing: list[str] = []
        package_root = Path(__file__).parents[1] / "agent_forge"
        for path in sorted(package_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                parameters = [
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                ]
                parameters = [item for item in parameters if item.arg not in {"self", "cls"}]
                parameters_typed = all(item.annotation is not None for item in parameters)
                variadics_typed = (
                    (node.args.vararg is None or node.args.vararg.annotation is not None)
                    and (node.args.kwarg is None or node.args.kwarg.annotation is not None)
                )
                if node.returns is None or not parameters_typed or not variadics_typed:
                    relative = path.relative_to(package_root.parent)
                    missing.append(f"{relative}:{node.lineno}:{node.name}")
        self.assertEqual(missing, [], "Production functions missing type contracts")

    def test_checkpoint_trace_keeps_the_existing_flat_json_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonTaskStateRepository(Path(tmp) / "task-state")
            checkpoint = store.start("run-1", "inspect project", tmp, "CodingAgent")
            trace = TraceRecorder(str(Path(tmp) / "trace.json"))

            trace.record_task_state_checkpoint(
                step=0,
                agent_name="CodingAgent",
                checkpoint=checkpoint,
            )

            event = trace.events[0]
            self.assertEqual(event["event_type"], "task_state_checkpoint")
            self.assertEqual(event["task_state"]["run_id"], "run-1")
            self.assertNotIn("data", event)

    def test_trace_payload_cannot_replace_envelope_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = TraceRecorder(str(Path(tmp) / "trace.json"))
            with self.assertRaisesRegex(ValueError, "cannot overwrite envelope"):
                trace.add(0, "CodingAgent", "error", run_id="fake-run")


if __name__ == "__main__":
    unittest.main()
