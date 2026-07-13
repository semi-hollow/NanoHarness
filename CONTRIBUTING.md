# Contributing

NanoHarness optimizes for local readability: a reader should understand a
function from its name, signature, nearby domain types, and one level of calls.

Keep the repository focused on the runtime control plane. Contributions should
not turn it into an IDE product, hosted service, or model-training stack.

## Local Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip setuptools wheel
python -m pip install -e '.[bench,dev]'
```

## Code Rules

1. Give every production function complete parameter and return annotations.
2. Prefer a named dataclass or Enum for runtime-owned state.
3. Keep `Any` at untrusted JSON, HTTP, MCP, UI, or provider boundaries.
4. Validate boundary data before passing it into owned runtime state.
5. Do not use `**kwargs`, `setattr`, or string keys to hide core state transitions.
6. Do not reuse one variable name for values of different types or meanings.
7. Serialize with `to_dict` at the storage boundary, not in distant callers.
8. Give high-value trace events a named `record_*` method with a typed signature.
9. Keep side effects visible in the function that owns them.
10. Add a regression test and update the failure-driven improvement log for behavioral changes.
11. Mark each user-visible capability's orchestration method with `PRIMARY ENTRYPOINT`.
12. Mark public persistence/policy/evidence boundaries called across modules with `RUNTIME PORT`.
13. In entrypoint docstrings, name the caller, the next owning component, and the evidence or return value.

Do not mark every public method. Constructors, data accessors, renderers, and
storage helpers remain unmarked unless they are a real cross-module boundary.
Multi-actor state machines may have multiple primary entries; document the
transition between them in `docs/guides/code-reading-map.md`.

## Verification

```bash
python -m pip install -e '.[dev]'
scripts/verify.sh
```

The verification path compiles the package, runs mypy across `agent_forge`, and
runs the behavioral regression suite. See
`docs/guides/code-reading-map.md` before changing runtime contracts.

Run `scripts/verify_mcp.sh` as well when MCP behavior changes. Real-model smoke
checks run automatically when `DEEPSEEK_API_KEY` is available.

## Repository Hygiene

- Keep changes scoped to one owning layer where possible.
- Do not commit API keys, `.env`, generated `.agent_forge` artifacts, or personal IDE state.
- Prefer public benchmark tasks and trace evidence over invented success claims.
- Update architecture or evaluation docs when public behavior changes.

## Pull Request Checklist

- [ ] `scripts/verify.sh` passes.
- [ ] `scripts/verify_mcp.sh` passes when MCP behavior changes.
- [ ] Mypy and the type-contract regression test pass.
- [ ] Capability entrypoints and runtime ports remain visible with method bodies collapsed.
- [ ] README or docs reflect user-visible behavior.
- [ ] No secret, personal path, or generated run artifact is tracked.
- [ ] New runtime behavior has trace/evaluation evidence and a failure-log case.
