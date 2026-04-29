from pathlib import Path
def build_repo_map(root):
 return "\n".join(sorted(str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file() and all(x not in str(p) for x in [".git","__pycache__","node_modules","target","dist","build"])))
