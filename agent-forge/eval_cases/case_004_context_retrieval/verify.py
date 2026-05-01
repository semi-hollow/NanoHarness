from agent_forge.context.repo_map import build_repo_map
from agent_forge.context.rag import retrieve
import json
m=build_repo_map('.')
r=retrieve('calculator',m.splitlines())
ok='calculator' in m and bool(r)
print(json.dumps({"task_success":ok,"test_pass":ok,"safety_violation":False,"notes":"context retrieval found calculator"}))
raise SystemExit(0 if ok else 1)
