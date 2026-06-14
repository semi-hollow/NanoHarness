# Provider Comparison

Agent Forge supports MockLLM for deterministic local verification and
OpenAI-compatible providers for real-model runs. This report records the first
public provider comparison based on committed artifacts.

## Provider Modes

| provider mode | command | purpose |
|---|---|---|
| MockLLM | `scripts/verify.sh` | Free deterministic verification for CI, WSL, and offline machines. |
| DeepSeek V4 Flash | `local_scripts/run_webhook_deepseek.sh` | Low-cost real-model WebhookPatchBench run. |
| OpenAI-compatible | `python run_demo.py --llm openai --base-url ... --model ...` | Generic path for Ollama, company gateways, or online APIs. |
| MCP hosted search wrappers | `AGENT_FORGE_WEB_PROVIDER=openai|claude` with `--mcp-config` | Optional external information lookup behind the MCP tool boundary. |

## Committed DeepSeek Artifacts

Source:

```text
docs/run-artifacts/single-deepseek/usage_report.md
docs/run-artifacts/webhook-deepseek/usage_report.md
```

| scenario | model | llm calls | input tokens | output tokens | total tokens | cache hit rate | estimated cost | LLM latency | tool calls |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| calculator smoke | `deepseek-v4-flash` | 7 | 18,513 | 880 | 19,393 | 18.67% | $0.002363 | 12,789 ms | 7, failed 3 |
| WebhookPatchBench | `deepseek-v4-flash` | 5 | 22,228 | 1,790 | 24,018 | 20.15% | $0.002998 | 17,230 ms | 11, failed 0 |

## Observations

- The realistic webhook scenario used more total tokens but fewer model calls
  than the calculator smoke run because it selected richer context and reached a
  clean validation path.
- Tool failures are not always bad. The calculator run shows failed diagnostics
  and command observations that were recovered from; this is exactly why failed
  observations are kept in trace and usage reports.
- WebhookPatchBench is the better public demonstration because it has security,
  reliability, side-effect ordering, test validation, and committed trace
  evidence.

## Provider Selection Rules

| situation | recommended provider |
|---|---|
| CI or company machine with no external API | MockLLM |
| Personal local validation with low cost | DeepSeek V4 Flash |
| Local model experiment | Ollama through OpenAI-compatible API |
| Company model gateway | OpenAI-compatible mode with company base URL |
| Fresh external information | MCP `forge.web_search` with explicit network enablement |

## Next Comparison Step

The next maturity step is a small provider matrix:

```text
.agent_forge/provider_runs/
  mock/
  deepseek/
  ollama/
  company-compatible/
```

Each provider run should capture:

- pass/fail status
- stop reason
- model call count
- input/output/cache tokens
- estimated cost
- latency
- failed tool observations
- final validation command

This would make provider tradeoffs auditable without hard-coding provider
preferences into the runtime.

