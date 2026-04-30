from agent_forge.context.repo_map import build_repo_map
from agent_forge.context.rag import retrieve
m=build_repo_map('.')
r=retrieve('calculator',m.splitlines())
raise SystemExit(0 if 'calculator' in m and r else 1)
