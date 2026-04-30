from agent_forge.context.symbol_search import symbol_search

hits = symbol_search("add", ".")
raise SystemExit(0 if any(hit.name == "add" and hit.kind == "def" for hit in hits) else 1)
