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


@dataclass(frozen=True)
class BreakpointTarget:
    label: str
    relative_path: str
    class_name: str
    function_name: str


TARGETS = (
    BreakpointTarget("Facade", "agent_forge/harness.py", "Harness", "run"),
    BreakpointTarget(
        "Runtime loop",
        "agent_forge/runtime/application/agent_loop.py",
        "AgentLoop",
        "run",
    ),
    BreakpointTarget(
        "Turn preparation",
        "agent_forge/runtime/application/turn_preparation.py",
        "TurnPreparation",
        "execute",
    ),
    BreakpointTarget(
        "Model boundary",
        "agent_forge/runtime/application/agent_loop.py",
        "AgentLoop",
        "_call_model",
    ),
    BreakpointTarget(
        "Tool intent",
        "agent_forge/runtime/application/tool_execution.py",
        "ToolExecutionPipeline",
        "_execute_call",
    ),
    BreakpointTarget(
        "Operation identity",
        "agent_forge/runtime/application/operation_tracker.py",
        "OperationTracker",
        "describe",
    ),
    BreakpointTarget(
        "Approval gate",
        "agent_forge/runtime/application/tool_authorization.py",
        "ToolAuthorizationGate",
        "_resolve_approval",
    ),
    BreakpointTarget(
        "Real pytest",
        "agent_forge/tools/diagnostics.py",
        "DiagnosticsTool",
        "execute",
    ),
    BreakpointTarget(
        "Durable stop",
        "agent_forge/runtime/application/run_lifecycle.py",
        "RunLifecycle",
        "stop",
    ),
    BreakpointTarget(
        "Benchmark orchestration",
        "agent_forge/bench/application/swebench.py",
        "RunSwebench",
        "execute",
    ),
    BreakpointTarget(
        "Case runtime",
        "agent_forge/bench/adapters/case_runtime.py",
        "LocalCaseExecutor",
        "run",
    ),
    BreakpointTarget(
        "Local evidence",
        "agent_forge/bench/adapters/local_validation.py",
        "",
        "read_local_validation",
    ),
    BreakpointTarget(
        "Official oracle",
        "agent_forge/bench/adapters/official_evaluator.py",
        "SwebenchOfficialEvaluator",
        "evaluate",
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
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
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
    return _first_executable_line(function)


def resolve_breakpoints(root: Path = PROJECT_ROOT) -> list[dict[str, object]]:
    return [
        {
            "label": target.label,
            "url": f"file://$PROJECT_DIR$/{target.relative_path}",
            "line": _target_line(root, target),
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
    next_timestamp = max(
        (_timestamp(node) for node in container.findall("line-breakpoint")),
        default=0,
    ) + 1
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


def _pycharm_is_running() -> bool:
    if sys.platform != "darwin":
        return False
    result = subprocess.run(
        ["ps", "-axo", "comm="],
        check=False,
        capture_output=True,
        text=True,
    )
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
    pycharm_open = _pycharm_is_running()
    if not args.dry_run and pycharm_open:
        print(
            "PyCharm is open. Close it, rerun this installer once, then reopen the "
            "NanoHarness project; no manual breakpoint setup is needed.",
            file=sys.stderr,
        )
        raise SystemExit(3)
    if not args.dry_run:
        items = install_breakpoints()
    action = "resolved" if args.dry_run else "installed"
    print(f"PyCharm Debug Lab breakpoints {action}: {len(items)}")
    for item in items:
        print(f"- {item['label']}: {item['url']}:{int(item['line']) + 1}")


if __name__ == "__main__":
    main()
