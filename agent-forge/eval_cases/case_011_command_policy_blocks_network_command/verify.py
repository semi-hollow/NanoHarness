from agent_forge.safety.command_policy import check_command

allowed, reason = check_command("curl https://example.com")
raise SystemExit(0 if (not allowed and "dangerous command blocked" in reason) else 1)
