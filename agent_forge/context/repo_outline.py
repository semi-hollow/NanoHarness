"""为已经排好序的 Python 文件生成有界 AST 结构摘要。"""

from __future__ import annotations

import ast
from pathlib import Path

MAX_SOURCE_CHARS = 200_000
MAX_MEMBERS_PER_CLASS = 12


def build_repo_outline(
    root: str | Path,
    ranked_files: list[str],
    *,
    max_files: int = 6,
    max_chars: int = 4_000,
) -> str:
    """只解析已进入 ranker 前列的 Python 文件，不做第二次全仓扫描。"""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    root_path = Path(root).resolve()
    blocks: list[str] = []
    for relative in ranked_files[: max(0, max_files)]:
        if not relative.endswith((".py", ".pyi")):
            continue
        candidate = (root_path / relative).resolve()
        if not candidate.is_relative_to(root_path) or not candidate.is_file():
            continue
        try:
            source = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if len(source) > MAX_SOURCE_CHARS:
            blocks.append(
                f"### {relative}\n- skipped: source exceeds AST outline limit"
            )
            continue
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError:
            blocks.append(f"### {relative}\n- skipped: Python syntax unavailable")
            continue

        declarations = _declarations(tree)
        if declarations:
            blocks.append(f"### {relative}\n" + "\n".join(declarations))
        if sum(len(block) + 2 for block in blocks) >= max_chars:
            break

    outline = "\n\n".join(blocks)
    if len(outline) <= max_chars:
        return outline
    marker = "\n... repo outline truncated ...\n"
    head = max(0, (max_chars - len(marker)) * 2 // 3)
    tail = max(0, max_chars - len(marker) - head)
    tail_text = outline[-tail:] if tail else ""
    return (outline[:head] + marker + tail_text)[:max_chars]


def _declarations(tree: ast.Module) -> list[str]:
    lines: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            lines.append(
                f"- {prefix} {node.name}{_signature(node)} [line {node.lineno}]"
            )
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(_name(base) for base in node.bases if _name(base))
            suffix = f"({bases})" if bases else ""
            lines.append(f"- class {node.name}{suffix} [line {node.lineno}]")
            members = [
                member
                for member in node.body
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            for member in members[:MAX_MEMBERS_PER_CLASS]:
                prefix = (
                    "async def" if isinstance(member, ast.AsyncFunctionDef) else "def"
                )
                lines.append(
                    f"  - {prefix} {member.name}{_signature(member)} "
                    f"[line {member.lineno}]"
                )
            if len(members) > MAX_MEMBERS_PER_CLASS:
                lines.append(
                    f"  - ... {len(members) - MAX_MEMBERS_PER_CLASS} more methods"
                )
    return lines


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    positional = [*node.args.posonlyargs, *node.args.args]
    names = [argument.arg for argument in positional[:6]]
    if node.args.vararg:
        names.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg:
        names.append(f"**{node.args.kwarg.arg}")
    if len(positional) > 6:
        names.append("...")
    return f"({', '.join(names)})"


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""
