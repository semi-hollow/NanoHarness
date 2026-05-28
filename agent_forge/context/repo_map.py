from pathlib import Path


IGNORE = {
    ".git",
    ".agent_forge",
    ".idea",
    ".venv",
    "__pycache__",
    "node_modules",
    "target",
    "dist",
    "build",
}
GENERATED_NAMES = {"agent_forge_trace.json", "eval_report.md", "summary.md"}


def build_repo_map(root):
    """Return a stable newline-separated file map for context assembly."""

    root_path = Path(root)
    files = []
    for path in root_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORE or part.endswith(".egg-info") for part in path.parts):
            continue
        if _is_generated(path):
            continue
        files.append(str(path.relative_to(root_path)))
    return "\n".join(sorted(files))


def _is_generated(path: Path) -> bool:
    """Keep generated run artifacts out of prompt retrieval."""

    name = path.name
    return (
        name in GENERATED_NAMES
        or name.endswith(".pyc")
        or name.endswith(".egg-info")
        or name.startswith("trace-")
        or name.endswith("_trace.json")
        or name.endswith(".pretty.json")
    )
