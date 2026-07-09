# Case Study: astropy__astropy-12907

## Why this case matters

This case is a compact real-repository example for studying Coding Agent tool contracts, candidate patch evidence, and conservative evaluation claims.

## Runtime lesson

The agent needs to inspect a narrow code window around the separability logic. If `read_file` ignores natural `offset` / `limit` arguments, the model may repeatedly inspect the wrong part of the file. This is a tool schema mismatch, not just a prompt issue.

## Evidence to collect

- `trace.json`: file inspection steps and tool arguments.
- `patch.diff`: candidate change.
- `usage.json`: tool calls, failed tools, and cost.
- `report.md`: failure class and next action.

## Boundary

A candidate patch is not an official SWE-bench resolution. Only claim `official_resolved` after official harness evaluation accepts the patch.
