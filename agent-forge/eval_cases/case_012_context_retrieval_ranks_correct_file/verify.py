from agent_forge.context.file_ranker import rank_files
from agent_forge.context.repo_map import build_repo_map

ranked = rank_files("fix calculator add", build_repo_map(".").splitlines(), ".")
raise SystemExit(0 if "examples/demo_repo/src/calculator.py" in ranked[:5] else 1)
