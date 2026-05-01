from agent_forge.context.symbol_search import symbol_search
import json

hits = symbol_search("add", ".")
ok=any(hit.name == "add" and hit.kind == "def" for hit in hits)
print(json.dumps({"task_success":ok,"test_pass":ok,"safety_violation":False,"notes":"symbol search found add function"}))
raise SystemExit(0 if ok else 1)
