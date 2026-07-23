"""源码符号的自底向上导航卡。

Code Compass 用 AST 提供可验证的定义、静态 caller/callee 和测试引用；黄金主链 owner
再通过 docstring 补充规范上游、下一 owner、证据和系统不变量。动态注入边只按显式说明
展示，不把静态分析包装成完整运行时调用图。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SymbolCard:
    """一个生产符号的源码位置、架构角色和可追溯依赖。"""

    symbol: str
    kind: str
    source_path: Path
    line_number: int
    layer: str
    purpose: str
    flow_position: str = ""
    canonical_upstream: str = ""
    next_owner: str = ""
    state_or_evidence: str = ""
    invariant: str = ""
    deletion_impact: str = ""
    static_callers: tuple[str, ...] = ()
    direct_callees: tuple[str, ...] = ()
    behavior_tests: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Definition:
    symbol: str
    node: ast.AST
    path: Path
    module: str
    kind: str
    docstring: str


def inspect_symbol(
    query: str,
    *,
    project_root: str | Path | None = None,
) -> SymbolCard:
    """解析一个类、方法或函数，并返回不夸大动态边的导航卡。"""

    root = Path(project_root) if project_root is not None else Path(__file__).parent.parent
    definitions = _definitions(root)
    normalized = _normalize_query(query)
    matches = [
        definition
        for definition in definitions
        if definition.symbol == normalized
        or definition.symbol.endswith(f".{normalized}")
        or _short_symbol(definition.symbol) == normalized
    ]
    if not matches:
        raise ValueError(f"source symbol not found: {query}")
    if len(matches) > 1:
        exact = [item for item in matches if _short_symbol(item.symbol) == normalized]
        if len(exact) == 1:
            matches = exact
        else:
            candidates = ", ".join(item.symbol for item in matches[:8])
            raise ValueError(f"source symbol is ambiguous: {query}; candidates: {candidates}")
    selected = matches[0]
    callers = _static_callers(definitions, selected)
    callees = _direct_callees(selected.node)
    fields = _compass_fields(selected.docstring)
    tests = _test_references(root, selected)
    relative = selected.path.relative_to(root)
    return SymbolCard(
        symbol=selected.symbol,
        kind=selected.kind,
        source_path=relative,
        line_number=int(getattr(selected.node, "lineno", 1)),
        layer=_layer(relative),
        purpose=_purpose(selected.docstring),
        flow_position=fields.get("流程位置", ""),
        canonical_upstream=fields.get("规范上游", "") or _static_hint(callers),
        next_owner=fields.get("下一 owner", "") or _static_hint(callees),
        state_or_evidence=fields.get("状态与证据", ""),
        invariant=fields.get("系统不变量", ""),
        deletion_impact=fields.get("删除/内联影响", ""),
        static_callers=callers,
        direct_callees=callees,
        behavior_tests=tests,
    )


def render_symbol_card(card: SymbolCard) -> str:
    """渲染紧凑导航卡；缺少 Compass 字段时明确显示待治理。"""

    def value(text: str) -> str:
        return text or "未登记；请结合静态引用判断是否应补充、私有化或删除"

    lines = [
        "# Code Compass",
        "",
        f"- symbol: `{card.symbol}`",
        f"- source: `{card.source_path.as_posix()}:{card.line_number}`",
        f"- kind / layer: `{card.kind}` / `{card.layer}`",
        f"- purpose: {value(card.purpose)}",
        f"- 流程位置: {value(card.flow_position)}",
        f"- 规范上游: {value(card.canonical_upstream)}",
        f"- 下一 owner: {value(card.next_owner)}",
        f"- 状态与证据: {value(card.state_or_evidence)}",
        f"- 系统不变量: {value(card.invariant)}",
        f"- 删除/内联影响: {value(card.deletion_impact)}",
        "",
        "## 静态关系（不等同完整运行时图）",
        "",
        f"- callers: `{', '.join(card.static_callers) or '-'}`",
        f"- callees: `{', '.join(card.direct_callees) or '-'}`",
        f"- tests: `{', '.join(card.behavior_tests) or '-'}`",
    ]
    return "\n".join(lines) + "\n"


def _definitions(root: Path) -> list[_Definition]:
    package = root / "agent_forge"
    definitions: list[_Definition] = []
    for path in sorted(package.rglob("*.py")):
        module = ".".join(path.relative_to(root).with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        definitions.extend(_collect_definitions(tree, path, module))
    return definitions


def _collect_definitions(
    tree: ast.Module,
    path: Path,
    module: str,
) -> list[_Definition]:
    result: list[_Definition] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_symbol = f"{module}.{node.name}"
            result.append(
                _Definition(
                    class_symbol,
                    node,
                    path,
                    module,
                    "class",
                    ast.get_docstring(node) or "",
                )
            )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result.append(
                        _Definition(
                            f"{class_symbol}.{child.name}",
                            child,
                            path,
                            module,
                            "method",
                            ast.get_docstring(child) or "",
                        )
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.append(
                _Definition(
                    f"{module}.{node.name}",
                    node,
                    path,
                    module,
                    "function",
                    ast.get_docstring(node) or "",
                )
            )
    return result


def _static_callers(
    definitions: list[_Definition],
    selected: _Definition,
) -> tuple[str, ...]:
    parts = selected.symbol.split(".")
    leaf = parts[-1]
    owner = parts[-2] if selected.kind == "method" else ""
    owner_aliases = {owner, _snake_case(owner)} - {""}
    callers: list[str] = []
    for definition in definitions:
        if definition.symbol == selected.symbol:
            continue
        called = [
            _call_path(node.func)
            for node in ast.walk(definition.node)
            if isinstance(node, ast.Call)
        ]
        if selected.kind == "method":
            matched = any(
                path.endswith(f".{leaf}")
                and any(alias in path.split(".") for alias in owner_aliases)
                for path in called
            )
        else:
            matched = any(path == leaf or path.endswith(f".{leaf}") for path in called)
        if matched:
            callers.append(_short_symbol(definition.symbol))
    return tuple(dict.fromkeys(callers[:12]))


def _direct_callees(node: ast.AST) -> tuple[str, ...]:
    names = [
        name
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        for name in [_call_name(child.func)]
        if name and name not in {"str", "int", "list", "tuple", "dict", "len", "bool"}
    ]
    return tuple(dict.fromkeys(names[:16]))


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _call_path(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_path(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _test_references(root: Path, selected: _Definition) -> tuple[str, ...]:
    tests = root / "tests"
    if not tests.is_dir():
        return ()
    parts = selected.symbol.split(".")
    owner = parts[-2] if selected.kind == "method" else ""
    needles = {selected.path.stem, owner, _snake_case(owner)} - {""}
    matches = [
        path.relative_to(root).as_posix()
        for path in sorted(tests.glob("test_*.py"))
        if any(needle in path.read_text(encoding="utf-8") for needle in needles)
    ]
    return tuple(matches[:10])


def _compass_fields(docstring: str) -> dict[str, str]:
    labels = (
        "流程位置",
        "规范上游",
        "下一 owner",
        "状态与证据",
        "系统不变量",
        "删除/内联影响",
    )
    fields: dict[str, str] = {}
    for line in docstring.splitlines():
        normalized = line.strip()
        for label in labels:
            prefix = f"{label}："
            if normalized.startswith(prefix):
                fields[label] = normalized.removeprefix(prefix).strip()
    flattened = " ".join(line.strip() for line in docstring.splitlines() if line.strip())
    label_pattern = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(
        rf"(?P<label>{label_pattern})\s*(?:依次是|：|:|是)\s*"
        rf"(?P<value>.*?)(?=(?:{label_pattern})\s*(?:依次是|：|:|是)|$)"
    )
    for match in pattern.finditer(flattened):
        value = match.group("value").strip(" ；;。")
        fields[match.group("label")] = value
    return fields


def _static_hint(symbols: tuple[str, ...]) -> str:
    if not symbols:
        return ""
    return f"静态候选（需结合依赖注入确认）：{', '.join(symbols)}"


def _snake_case(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def _purpose(docstring: str) -> str:
    return next((line.strip() for line in docstring.splitlines() if line.strip()), "")


def _short_symbol(symbol: str) -> str:
    parts = symbol.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else symbol


def _normalize_query(query: str) -> str:
    return query.strip().replace("::", ".").replace(":", ".").removesuffix("()")


def _layer(path: Path) -> str:
    parts = set(path.parts)
    for name in ("domain", "application", "ports", "adapters", "presentation", "cli"):
        if name in parts:
            return name
    return "public facade" if path.name == "harness.py" else "capability"


__all__ = ["SymbolCard", "inspect_symbol", "render_symbol_card"]
