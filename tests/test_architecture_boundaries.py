"""Executable dependency rules from docs/ARCHITECTURE.md."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "agent_forge"
RUNTIME_ROOT = PACKAGE_ROOT / "runtime"
EVALUATION_ROOT = PACKAGE_ROOT / "evaluation"
BENCH_ROOT = PACKAGE_ROOT / "bench"
OBSERVABILITY_ROOT = PACKAGE_ROOT / "observability"
WORKBENCH_ROOT = PACKAGE_ROOT / "workbench"
CLI_ROOT = PACKAGE_ROOT / "cli"


def _absolute_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.append((node.lineno, node.module))
    return imports


class ArchitectureBoundaryTest(unittest.TestCase):
    def test_production_package_never_imports_test_support(self) -> None:
        violations: list[str] = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            for line, imported in _absolute_imports(path):
                if imported == "tests" or imported.startswith("tests."):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{line} -> {imported}"
                    )
        self.assertEqual(
            violations,
            [],
            "Production code must not depend on tests/support or test fixtures",
        )

    def test_runtime_domain_has_no_outward_dependencies(self) -> None:
        allowed = ("agent_forge.contracts", "agent_forge.runtime.domain")
        violations: list[str] = []
        for path in sorted((RUNTIME_ROOT / "domain").glob("*.py")):
            for line, imported in _absolute_imports(path):
                if imported.startswith("agent_forge") and not imported.startswith(allowed):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{line} -> {imported}")
        self.assertEqual(violations, [], "Runtime Domain must remain infrastructure-free")

    def test_runtime_ports_depend_only_on_contracts_and_domain(self) -> None:
        allowed = (
            "agent_forge.contracts",
            "agent_forge.context.contracts",
            "agent_forge.runtime.domain",
            "agent_forge.observability.event",
            "agent_forge.observability.domain.event",
        )
        violations: list[str] = []
        for path in sorted((RUNTIME_ROOT / "ports").glob("*.py")):
            for line, imported in _absolute_imports(path):
                if imported.startswith("agent_forge") and not imported.startswith(allowed):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{line} -> {imported}")
        self.assertEqual(violations, [], "Runtime Ports must not know concrete implementations")

    def test_runtime_application_does_not_import_adapters_or_concrete_stores(self) -> None:
        forbidden = (
            "agent_forge.runtime.adapters",
            "agent_forge.runtime.approval",
            "agent_forge.runtime.human_input",
            "agent_forge.runtime.operation_ledger",
            "agent_forge.runtime.task_state",
            "agent_forge.runtime.execution_environment",
            "agent_forge.runtime.wiring",
            "agent_forge.context.context_builder",
            "agent_forge.context.repo_map",
            "agent_forge.skills",
            "agent_forge.observability.trace",
            "agent_forge.tools.registry",
        )
        violations: list[str] = []
        for path in sorted((RUNTIME_ROOT / "application").glob("*.py")):
            for line, imported in _absolute_imports(path):
                if imported.startswith(forbidden):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{line} -> {imported}")
        self.assertEqual(violations, [], "Runtime Application must depend on Ports, not adapters")

    def test_only_runtime_composition_imports_runtime_adapters(self) -> None:
        allowed = {
            Path("agent_forge/runtime/wiring.py"),
            Path("agent_forge/runtime/adapters/__init__.py"),
        }
        violations: list[str] = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            relative = path.relative_to(PROJECT_ROOT)
            if relative in allowed or "runtime/adapters" in relative.as_posix():
                continue
            for line, imported in _absolute_imports(path):
                if imported.startswith("agent_forge.runtime.adapters"):
                    violations.append(f"{relative}:{line} -> {imported}")
        self.assertEqual(violations, [], "Concrete adapters must be assembled in runtime.wiring")

    def test_removed_compatibility_facades_do_not_return(self) -> None:
        removed = [
            "agent_forge/ui.py",
            "agent_forge/runtime/agent_loop.py",
            "agent_forge/runtime/approval.py",
            "agent_forge/runtime/human_input.py",
            "agent_forge/runtime/message.py",
            "agent_forge/runtime/observation.py",
            "agent_forge/runtime/operation_ledger.py",
            "agent_forge/runtime/run_lifecycle.py",
            "agent_forge/runtime/state.py",
            "agent_forge/runtime/task_state.py",
            "agent_forge/runtime/tool_call.py",
            "agent_forge/runtime/tool_execution.py",
            "agent_forge/bench/swebench.py",
            "agent_forge/multi_agent/coordinator.py",
            "agent_forge/multi_agent/live_fanout.py",
        ]
        present = [path for path in removed if (PROJECT_ROOT / path).exists()]
        self.assertEqual(present, [], "旧兼容入口会让正式调用路径重新变得含糊")

    def test_runtime_public_api_and_layers_exist(self) -> None:
        expected = [
            RUNTIME_ROOT / "api.py",
            RUNTIME_ROOT / "application" / "agent_loop.py",
            RUNTIME_ROOT / "domain" / "task.py",
            RUNTIME_ROOT / "ports" / "repositories.py",
            RUNTIME_ROOT / "ports" / "context.py",
            RUNTIME_ROOT / "ports" / "skills.py",
            RUNTIME_ROOT / "adapters" / "task_state_json.py",
            RUNTIME_ROOT / "adapters" / "context_assembler.py",
            RUNTIME_ROOT / "wiring.py",
        ]
        self.assertEqual([str(path) for path in expected if not path.is_file()], [])

    def test_multi_agent_domain_has_no_outward_dependencies(self) -> None:
        root = PACKAGE_ROOT / "multi_agent" / "domain"
        allowed = (
            "agent_forge.contracts",
            "agent_forge.multi_agent.domain",
        )
        violations: list[str] = []
        for path in sorted(root.glob("*.py")):
            for line, imported in _absolute_imports(path):
                if imported.startswith("agent_forge") and not imported.startswith(allowed):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{line} -> {imported}"
                    )
        self.assertEqual(violations, [], "Multi-Agent Domain must remain pure")

    def test_multi_agent_application_does_not_import_adapters(self) -> None:
        root = PACKAGE_ROOT / "multi_agent" / "application"
        forbidden = (
            "agent_forge.multi_agent.adapters",
            "agent_forge.multi_agent.wiring",
            "agent_forge.observability.trace",
            "agent_forge.runtime.execution_environment",
            "agent_forge.runtime.api",
            "agent_forge.tools.registry",
        )
        violations: list[str] = []
        for path in sorted(root.glob("*.py")):
            for line, imported in _absolute_imports(path):
                if imported.startswith(forbidden):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{line} -> {imported}"
                    )
        self.assertEqual(
            violations,
            [],
            "Multi-Agent Application must execute side effects through ports",
        )

    def test_multi_agent_public_api_and_layers_exist(self) -> None:
        root = PACKAGE_ROOT / "multi_agent"
        expected = [
            root / "api.py",
            root / "application" / "coordinator.py",
            root / "application" / "live_fanout.py",
            root / "domain" / "fanout.py",
            root / "domain" / "live.py",
            root / "ports" / "live.py",
            root / "adapters" / "local_worker.py",
            root / "presentation" / "live_report.py",
            root / "wiring.py",
        ]
        self.assertEqual([str(path) for path in expected if not path.is_file()], [])

    def test_evaluation_domain_has_no_outward_dependencies(self) -> None:
        allowed = (
            "agent_forge.contracts",
            "agent_forge.evaluation.domain",
        )
        violations: list[str] = []
        for path in sorted((EVALUATION_ROOT / "domain").glob("*.py")):
            for line, imported in _absolute_imports(path):
                if imported.startswith("agent_forge") and not imported.startswith(allowed):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{line} -> {imported}"
                    )
        self.assertEqual(violations, [], "Evaluation Domain must remain pure")

    def test_evaluation_application_does_not_import_adapters_or_presentation(self) -> None:
        forbidden = (
            "agent_forge.evaluation.adapters",
            "agent_forge.evaluation.presentation",
            "agent_forge.evaluation.wiring",
        )
        violations: list[str] = []
        for path in sorted((EVALUATION_ROOT / "application").glob("*.py")):
            for line, imported in _absolute_imports(path):
                if imported.startswith(forbidden):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{line} -> {imported}"
                    )
        self.assertEqual(
            violations,
            [],
            "Evaluation Application must use Ports instead of concrete artifacts",
        )

    def test_evaluation_public_api_and_layers_exist(self) -> None:
        expected = [
            EVALUATION_ROOT / "api.py",
            EVALUATION_ROOT / "application" / "scorecard.py",
            EVALUATION_ROOT / "domain" / "comparison.py",
            EVALUATION_ROOT / "domain" / "scorecard.py",
            EVALUATION_ROOT / "ports" / "evidence.py",
            EVALUATION_ROOT / "adapters" / "json_files.py",
            EVALUATION_ROOT / "presentation" / "scorecard_report.py",
            EVALUATION_ROOT / "wiring.py",
        ]
        self.assertEqual([str(path) for path in expected if not path.is_file()], [])

    def test_bench_domain_has_no_outward_dependencies(self) -> None:
        allowed = (
            "agent_forge.contracts",
            "agent_forge.bench.domain",
        )
        violations: list[str] = []
        for path in sorted((BENCH_ROOT / "domain").glob("*.py")):
            for line, imported in _absolute_imports(path):
                if imported.startswith("agent_forge") and not imported.startswith(allowed):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{line} -> {imported}"
                    )
        self.assertEqual(violations, [], "Benchmark Domain must remain pure")

    def test_bench_application_does_not_import_adapters_or_presentation(self) -> None:
        forbidden = (
            "agent_forge.bench.adapters",
            "agent_forge.bench.presentation",
            "agent_forge.bench.wiring",
        )
        violations: list[str] = []
        for path in sorted((BENCH_ROOT / "application").glob("*.py")):
            for line, imported in _absolute_imports(path):
                if imported.startswith(forbidden):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{line} -> {imported}"
                    )
        self.assertEqual(
            violations,
            [],
            "Benchmark Application must execute side effects through Ports",
        )

    def test_bench_public_api_and_layers_exist(self) -> None:
        expected = [
            BENCH_ROOT / "api.py",
            BENCH_ROOT / "application" / "swebench.py",
            BENCH_ROOT / "domain" / "models.py",
            BENCH_ROOT / "domain" / "failure_taxonomy.py",
            BENCH_ROOT / "ports" / "benchmark.py",
            BENCH_ROOT / "adapters" / "case_runtime.py",
            BENCH_ROOT / "adapters" / "official_evaluator.py",
            BENCH_ROOT / "presentation" / "report.py",
            BENCH_ROOT / "wiring.py",
        ]
        self.assertEqual([str(path) for path in expected if not path.is_file()], [])

    def test_observability_domain_has_no_outward_dependencies(self) -> None:
        violations: list[str] = []
        for path in sorted((OBSERVABILITY_ROOT / "domain").glob("*.py")):
            for line, imported in _absolute_imports(path):
                if imported.startswith("agent_forge") and not imported.startswith(
                    "agent_forge.observability.domain"
                ):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{line} -> {imported}"
                    )
        self.assertEqual(violations, [], "Evidence Domain must remain pure")

    def test_observability_domain_does_not_render_reports(self) -> None:
        violations: list[str] = []
        for path in sorted((OBSERVABILITY_ROOT / "domain").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    node.name.startswith("render_")
                    or node.name.startswith("write_")
                ):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} -> {node.name}"
                    )
        self.assertEqual(
            violations,
            [],
            "Evidence Domain projects facts; Presentation owns report rendering",
        )

    def test_observability_application_does_not_import_adapters_or_presentation(self) -> None:
        forbidden = (
            "agent_forge.observability.adapters",
            "agent_forge.observability.presentation",
        )
        violations: list[str] = []
        for path in sorted((OBSERVABILITY_ROOT / "application").glob("*.py")):
            for line, imported in _absolute_imports(path):
                if imported.startswith(forbidden):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{line} -> {imported}"
                    )
        self.assertEqual(
            violations,
            [],
            "Evidence Application must project facts without concrete storage",
        )

    def test_observability_public_api_and_layers_exist(self) -> None:
        expected = [
            OBSERVABILITY_ROOT / "api.py",
            OBSERVABILITY_ROOT / "application" / "usage.py",
            OBSERVABILITY_ROOT / "domain" / "event.py",
            OBSERVABILITY_ROOT / "domain" / "usage.py",
            OBSERVABILITY_ROOT / "adapters" / "json_trace.py",
            OBSERVABILITY_ROOT / "adapters" / "usage_files.py",
            OBSERVABILITY_ROOT / "presentation" / "usage_report.py",
        ]
        self.assertEqual([str(path) for path in expected if not path.is_file()], [])

    def test_workbench_domain_has_no_outward_dependencies(self) -> None:
        violations: list[str] = []
        for path in sorted((WORKBENCH_ROOT / "domain").glob("*.py")):
            for line, imported in _absolute_imports(path):
                if imported.startswith("agent_forge") and not imported.startswith(
                    "agent_forge.workbench.domain"
                ):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{line} -> {imported}"
                    )
        self.assertEqual(violations, [], "Workbench Domain must remain pure")

    def test_workbench_application_and_presentation_do_not_import_adapters(self) -> None:
        violations: list[str] = []
        for layer in ("application", "presentation"):
            for path in sorted((WORKBENCH_ROOT / layer).glob("*.py")):
                for line, imported in _absolute_imports(path):
                    if imported.startswith("agent_forge.workbench.adapters"):
                        violations.append(
                            f"{path.relative_to(PROJECT_ROOT)}:{line} -> {imported}"
                        )
        self.assertEqual(
            violations,
            [],
            "Workbench adapters must be assembled through workbench.wiring",
        )

    def test_cli_does_not_import_capability_adapters(self) -> None:
        forbidden = (
            "agent_forge.runtime.adapters",
            "agent_forge.multi_agent.adapters",
            "agent_forge.evaluation.adapters",
            "agent_forge.bench.adapters",
            "agent_forge.observability.adapters",
            "agent_forge.workbench.adapters",
        )
        violations: list[str] = []
        for path in sorted(CLI_ROOT.glob("*.py")):
            for line, imported in _absolute_imports(path):
                if imported.startswith(forbidden):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{line} -> {imported}"
                    )
        self.assertEqual(
            violations,
            [],
            "CLI must enter capabilities through public APIs/composition roots",
        )

    def test_single_agent_cli_delegates_to_harness_facade(self) -> None:
        repository_path = CLI_ROOT / "repository.py"
        tree = ast.parse(repository_path.read_text(encoding="utf-8"))
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        dispatcher = functions["run_repository_task"]
        single_run = functions["_run_single_repository_task"]

        dispatcher_calls = {
            node.func.id
            for node in ast.walk(dispatcher)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn(
            "_run_single_repository_task",
            dispatcher_calls,
            "The public single-agent CLI path must keep routing to its Harness adapter",
        )

        harness_assignments = [
            node
            for node in ast.walk(single_run)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "Harness"
        ]
        self.assertEqual(
            len(harness_assignments),
            1,
            "The single-agent CLI path must construct exactly one public Harness",
        )
        harness_target = harness_assignments[0].targets[0]
        self.assertIsInstance(harness_target, ast.Name)
        harness_name = harness_target.id
        harness_runs = [
            node
            for node in ast.walk(single_run)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == harness_name
        ]
        self.assertEqual(
            len(harness_runs),
            1,
            "CLI must delegate execution exactly once to Harness.run",
        )

        forbidden_calls = {
            "ExecutionEnvironment",
            "RuntimeConfig",
            "TraceRecorder",
            "_build_runtime_config",
            "_write_latest_run_pointer",
            "build_agent_loop_from_request",
            "build_registry",
            "build_task_state_repository",
            "prepare_execution_environment",
            "write_usage_artifacts",
        }
        direct_calls = {
            node.func.id
            for node in ast.walk(single_run)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertEqual(
            sorted(direct_calls & forbidden_calls),
            [],
            "CLI must not duplicate Runtime orchestration or Evidence publication",
        )

    def test_workbench_public_api_and_layers_exist(self) -> None:
        expected = [
            WORKBENCH_ROOT / "api.py",
            WORKBENCH_ROOT / "application" / "services.py",
            WORKBENCH_ROOT / "ports" / "services.py",
            WORKBENCH_ROOT / "adapters" / "evidence_files.py",
            WORKBENCH_ROOT / "presentation" / "http.py",
            WORKBENCH_ROOT / "wiring.py",
        ]
        self.assertEqual([str(path) for path in expected if not path.is_file()], [])


if __name__ == "__main__":
    unittest.main()
