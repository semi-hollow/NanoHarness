from agent_forge.safety.command_policy import check_command
ok, _ = check_command('rm -rf /tmp/x')
raise SystemExit(0 if not ok else 1)
