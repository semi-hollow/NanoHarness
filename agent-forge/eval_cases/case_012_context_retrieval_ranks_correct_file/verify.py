from agent_forge.context.file_ranker import rank_files
from agent_forge.context.repo_map import build_repo_map
import json

ranked = rank_files("fix calculator add", build_repo_map(".").splitlines(), ".")
ok="examples/demo_repo/src/calculator.py" in ranked[:5]
print(json.dumps({"task_success":ok,"test_pass":ok,"safety_violation":False,"notes":"file ranker placed calculator near top"}))
raise SystemExit(0 if ok else 1)
