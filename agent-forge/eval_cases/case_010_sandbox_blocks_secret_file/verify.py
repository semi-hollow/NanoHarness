from agent_forge.safety.sandbox import WorkspaceSandbox

try:
    WorkspaceSandbox(".").ensure_safe_path(".env")
except PermissionError as exc:
    raise SystemExit(0 if "sensitive file deny" in str(exc) else 1)
raise SystemExit(1)
