#!/usr/bin/env python3
"""按 symbol 自动定位并安装 NanoHarness Debug Lab 的 PyCharm 行断点。"""

from __future__ import annotations

import argparse
import ast
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAB_GROUP = "NanoHarness Debug Lab"

# 阅读范围只影响 IDE 展示：主路径隐藏 tests/support 和外围 Adapter，需要时再切换范围。
READING_SCOPES = (
    (
        "00_NanoHarness_Core_Owners.xml",
        "00 NanoHarness Core Owners",
        "file:agent_forge/harness.py"
        "||file:agent_forge/runtime/wiring.py"
        "||file:agent_forge/runtime/application/agent_loop.py"
        "||file:agent_forge/runtime/application/run_preparation.py"
        "||file:agent_forge/runtime/application/model_step_preparation.py"
        "||file:agent_forge/runtime/application/tool_execution.py"
        "||file:agent_forge/runtime/application/run_lifecycle.py"
        "||file:agent_forge/context/application/compaction.py"
        "||file:agent_forge/memory/application/service.py"
        "||file:agent_forge/multi_agent/application/fanout.py"
        "||file:agent_forge/multi_agent/application/live_handoff.py"
        "||file:agent_forge/bench/application/swebench.py",
    ),
    (
        "01_NanoHarness_All_Production.xml",
        "01 NanoHarness All Production",
        "file:agent_forge//*||file:apps//*",
    ),
    (
        "20_NanoHarness_Inbound_Apps.xml",
        "20 NanoHarness Inbound Apps",
        "file:apps//*",
    ),
    (
        "90_NanoHarness_Tests.xml",
        "90 NanoHarness Tests",
        "file:tests//*",
    ),
)


@dataclass(frozen=True, kw_only=True)
class BreakpointTarget:
    """一个按 Lab 场景启用的源码断点，不干扰其他运行配置。"""

    scenario: str
    label: str
    relative_path: str
    class_name: str
    function_name: str
    # 留空时停在函数第一条语句；填写后停在函数内包含该文本的状态边界。
    anchor: str = ""


TARGETS = (
    BreakpointTarget(
        scenario="governed",
        label="Lab 1 - Facade",
        relative_path="agent_forge/harness.py",
        class_name="Harness",
        function_name="run",
    ),
    BreakpointTarget(
        scenario="governed",
        label="Lab 1 - Runtime loop",
        relative_path="agent_forge/runtime/application/agent_loop.py",
        class_name="AgentLoop",
        function_name="run",
    ),
    BreakpointTarget(
        scenario="governed",
        label="Lab 1 - Tool intent",
        relative_path="agent_forge/runtime/application/tool_execution.py",
        class_name="ToolExecutionPipeline",
        function_name="_execute_call",
    ),
    BreakpointTarget(
        scenario="governed",
        label="Lab 1 - Operation identity",
        relative_path="agent_forge/runtime/application/operation_tracker.py",
        class_name="OperationTracker",
        function_name="build_operation_intent",
    ),
    BreakpointTarget(
        scenario="governed",
        label="Lab 1 - Approval gate",
        relative_path="agent_forge/runtime/application/tool_authorization.py",
        class_name="ToolAuthorizationGate",
        function_name="_resolve_approval",
    ),
    BreakpointTarget(
        scenario="governed",
        label="Lab 1 - Restricted validation",
        relative_path="agent_forge/tools/builtins/python_validation.py",
        class_name="PythonValidationTool",
        function_name="execute",
    ),
    BreakpointTarget(
        scenario="governed",
        label="Lab 1 - Durable stop",
        relative_path="agent_forge/runtime/application/run_lifecycle.py",
        class_name="RunLifecycle",
        function_name="finalize_run",
    ),
    BreakpointTarget(
        scenario="coordinated",
        label="Lab 2 - Fanout coordinator",
        relative_path="agent_forge/multi_agent/application/fanout.py",
        class_name="FanoutCoordinator",
        function_name="run",
    ),
    BreakpointTarget(
        scenario="coordinated",
        label="Lab 2 - Dependency batch",
        relative_path="agent_forge/multi_agent/application/fanout.py",
        class_name="FanoutCoordinator",
        function_name="_run_batch",
    ),
    BreakpointTarget(
        scenario="coordinated",
        label="Lab 2 - Isolated worker",
        relative_path="agent_forge/multi_agent/adapters/local_worker.py",
        class_name="LocalAgentWorkerAdapter",
        function_name="run_worker",
    ),
    BreakpointTarget(
        scenario="coordinated",
        label="Lab 2 - Scoped merge",
        relative_path="agent_forge/multi_agent/application/fanout.py",
        class_name="FanoutCoordinator",
        function_name="_merge_batch",
    ),
    BreakpointTarget(
        scenario="coordinated",
        label="Lab 2 - Finalizer",
        relative_path="agent_forge/multi_agent/adapters/local_worker.py",
        class_name="LocalAgentWorkerAdapter",
        function_name="run_finalizer",
    ),
)


def _first_executable_line(function: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    body = function.body
    if body and isinstance(body[0], ast.Expr):
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            body = body[1:]
    if not body:
        raise ValueError(f"{function.name} has no executable statement")
    return body[0].lineno - 1  # PyCharm workspace.xml 使用零基行号。


def _target_line(root: Path, target: BreakpointTarget) -> int:
    path = root / target.relative_path
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    scope: list[ast.stmt] = tree.body
    if target.class_name:
        owner = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == target.class_name
            ),
            None,
        )
        if owner is None:
            raise ValueError(f"class not found: {target.class_name} in {path}")
        scope = owner.body
    function = next(
        (
            node
            for node in scope
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == target.function_name
        ),
        None,
    )
    if function is None:
        raise ValueError(
            f"function not found: {target.class_name}.{target.function_name} in {path}"
        )
    if target.anchor:
        lines = source.splitlines()
        end_line = function.end_lineno or function.lineno
        matches = [
            line_number
            for line_number in range(function.lineno, end_line + 1)
            if target.anchor in lines[line_number - 1]
        ]
        if len(matches) != 1:
            raise ValueError(
                f"anchor must match exactly once: {target.anchor!r} in "
                f"{target.class_name}.{target.function_name} ({path}); "
                f"matched {len(matches)} lines"
            )
        return matches[0] - 1
    return _first_executable_line(function)


def resolve_breakpoints(root: Path = PROJECT_ROOT) -> list[dict[str, object]]:
    return [
        {
            "scenario": target.scenario,
            "label": target.label,
            "url": f"file://$PROJECT_DIR$/{target.relative_path}",
            "line": _target_line(root, target),
            "condition": (
                "__import__('os').environ.get('NANOHARNESS_DEBUG_LAB') "
                f"== '{target.scenario}'"
            ),
        }
        for target in TARGETS
    ]


def _load_workspace(path: Path) -> ET.ElementTree:
    if not path.exists():
        return ET.ElementTree(ET.Element("project", {"version": "4"}))
    try:
        return ET.parse(path)
    except ET.ParseError as exc:
        raise ValueError(f"invalid PyCharm workspace XML: {path}: {exc}") from exc


def _breakpoint_container(root: ET.Element) -> ET.Element:
    component = next(
        (
            item
            for item in root.findall("component")
            if item.get("name") == "XDebuggerManager"
        ),
        None,
    )
    if component is None:
        component = ET.SubElement(root, "component", {"name": "XDebuggerManager"})
    manager = component.find("breakpoint-manager")
    if manager is None:
        manager = ET.SubElement(component, "breakpoint-manager")
    breakpoints = manager.find("breakpoints")
    if breakpoints is None:
        breakpoints = ET.SubElement(manager, "breakpoints")
    return breakpoints


def _timestamp(node: ET.Element) -> int:
    for option in node.findall("option"):
        if option.get("name") == "timeStamp":
            try:
                return int(option.get("value", "0"))
            except ValueError:
                return 0
    return 0


def _breakpoint_key(node: ET.Element) -> tuple[str, int] | None:
    try:
        return node.findtext("url", default=""), int(
            node.findtext("line", default="-1")
        )
    except ValueError:
        return None


def install_breakpoints(
    root: Path = PROJECT_ROOT,
    workspace_path: Path | None = None,
) -> list[dict[str, object]]:
    workspace = workspace_path or root / ".idea" / "workspace.xml"
    idea = workspace.parent
    idea.mkdir(parents=True, exist_ok=True)
    backup = idea / "workspace.xml.before-nanoharness-debug-lab"
    resolved = resolve_breakpoints(root)
    tree = _load_workspace(workspace)
    project = tree.getroot()
    container = _breakpoint_container(project)

    # 只删除带本实验场 group 的断点；用户自己的条件断点和同位置断点都保留。
    for node in list(container.findall("line-breakpoint")):
        if node.findtext("group", default="") == LAB_GROUP:
            container.remove(node)

    existing = {
        key
        for node in container.findall("line-breakpoint")
        if node.get("type") == "python-line"
        and (key := _breakpoint_key(node)) is not None
    }
    next_timestamp = (
        max(
            (_timestamp(node) for node in container.findall("line-breakpoint")),
            default=0,
        )
        + 1
    )
    for index, item in enumerate(resolved):
        key = (str(item["url"]), int(item["line"]))
        if key in existing:
            continue
        node = ET.SubElement(
            container,
            "line-breakpoint",
            {"enabled": "true", "suspend": "THREAD", "type": "python-line"},
        )
        ET.SubElement(node, "url").text = key[0]
        ET.SubElement(node, "line").text = str(key[1])
        ET.SubElement(node, "group").text = LAB_GROUP
        ET.SubElement(node, "description").text = str(item["label"])
        ET.SubElement(
            node,
            "condition",
            {"expression": str(item["condition"]), "language": "Python"},
        )
        ET.SubElement(
            node,
            "option",
            {"name": "timeStamp", "value": str(next_timestamp + index)},
        )

    if workspace.exists() and not backup.exists():
        shutil.copy2(workspace, backup)
    ET.indent(tree, space="  ")
    temporary = workspace.with_suffix(".xml.tmp")
    tree.write(temporary, encoding="utf-8", xml_declaration=True)
    temporary.replace(workspace)
    return resolved


def install_reading_scopes(root: Path = PROJECT_ROOT) -> list[Path]:
    """安装可在 Project/Find Usages 中选择的低噪音阅读范围。"""

    scope_dir = root / ".idea" / "scopes"
    scope_dir.mkdir(parents=True, exist_ok=True)
    installed_scope_files: list[Path] = []
    for filename, scope_name, scope_pattern in READING_SCOPES:
        project = ET.Element("component", {"name": "DependencyValidationManager"})
        ET.SubElement(
            project,
            "scope",
            {"name": scope_name, "pattern": scope_pattern},
        )
        scope_file = scope_dir / filename
        scope_tree = ET.ElementTree(project)
        ET.indent(scope_tree, space="  ")
        scope_tree.write(
            scope_file,
            encoding="utf-8",
            xml_declaration=True,
        )
        installed_scope_files.append(scope_file)
    return installed_scope_files


def _pycharm_is_running() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["ps", "-axo", "comm="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        # 无法确认 IDE 状态时按“仍在运行”处理，避免 workspace.xml 被覆盖。
        return True
    if result.returncode != 0:
        return False
    return any(
        Path(command.strip()).name.lower().startswith("pycharm")
        for command in result.stdout.splitlines()
        if command.strip()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    items = resolve_breakpoints()
    if args.dry_run:
        action = "resolved"
    else:
        if _pycharm_is_running():
            print(
                "PyCharm is open. Close it, rerun this installer once, then reopen the "
                "NanoHarness project; no manual breakpoint setup is needed.",
                file=sys.stderr,
            )
            raise SystemExit(3)
        items = install_breakpoints()
        install_reading_scopes()
        action = "installed"
    print(f"PyCharm Debug Lab breakpoints {action}: {len(items)}")
    for item in items:
        print(f"- {item['label']}: {item['url']}:{int(item['line']) + 1}")


if __name__ == "__main__":
    main()
