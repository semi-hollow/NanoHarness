from pathlib import Path


IGNORE = {".git", "__pycache__", "node_modules", "target", "dist", "build"}


def build_repo_map(root):
    """Return a stable newline-separated file map for context assembly."""

    root_path = Path(root)
    files = []
    for path in root_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORE for part in path.parts):
            continue
        files.append(str(path.relative_to(root_path)))
    return "\n".join(sorted(files))
