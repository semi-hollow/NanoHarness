import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SymbolHit:
    name: str
    kind: str
    path: str
    line: int


def scan_python_symbols(root: str | Path) -> list[SymbolHit]:
    root_path = Path(root)
    hits: list[SymbolHit] = []
    for path in sorted(root_path.rglob("*.py")):
        if any(part in {".git", "__pycache__", ".venv", "venv"} for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                hits.append(SymbolHit(node.name, "class", str(path.relative_to(root_path)), node.lineno))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                hits.append(SymbolHit(node.name, "def", str(path.relative_to(root_path)), node.lineno))
    return sorted(hits, key=lambda h: (h.path, h.line, h.name))


def symbol_search(query: str, root: str | Path = ".") -> list[SymbolHit]:
    lowered = query.lower()
    return [hit for hit in scan_python_symbols(root) if lowered in hit.name.lower() or lowered in hit.path.lower()]


def render_symbols(hits: list[SymbolHit]) -> str:
    return "\n".join(f"{hit.path}:{hit.line}: {hit.kind} {hit.name}" for hit in hits)
