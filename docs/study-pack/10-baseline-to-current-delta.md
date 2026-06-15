# 10 Baseline To Current Delta

这份文件只回答一个问题：以 `codex/sourdaugh` 作为 baseline，当前 `schema`
分支相比 baseline 到底新增了什么、应该按什么顺序看、现在项目成熟到哪一步。

> 备注：你口头说的 `sourdough`，仓库里的实际分支名是 `codex/sourdaugh`。

## Diff 范围

```bash
git diff --stat codex/sourdaugh..schema
git log --oneline --reverse codex/sourdaugh..schema
```

当前增量：

| item | value |
|---|---:|
| baseline branch | `codex/sourdaugh` |
| baseline commit | `5651d3d Add field readiness agent capabilities` |
| current branch | `schema` |
| current commit | `16d5039 Add project profile drafting brief` |
| commits added | 7 |
| files changed | 61 |
| additions | 4820 |
| deletions | 2176 |

新增提交顺序：

```text
a5d5dff Add production runtime control plane
d6ba4ba Harden schema runtime controls and docs
d02d9a6 Add built-in MCP server and web tools
b6fd54a Document MCP external tools guide
cdd91c4 Add project profile guide
78c380e Complete open-source readiness package
16d5039 Add project profile drafting brief
```

## 一句话变化

`codex/sourdaugh` 已经有 AgentLoop、Context、Tool、Safety、Trace、Eval 的主体。
`schema` 把它推进成一个可公开展示的 production-style CodingAgent runtime core：

- 增加 runtime control plane：执行环境、approval hooks、task state、trace replay、review gate。
- 增加 MCP 外部工具：内置 stdio MCP server、tool discovery、web_search/web_fetch。
- 增强可观测和 eval：usage report、eval history、badcase flywheel、P0 回归测试。
- 补齐开源发布面：License、CI、Contributing、Security、Release checklist、README 首屏图和运行证据。
- 精简学习材料：删除旧的长篇重复文档，合并成更短的 study-pack 和 readiness docs。

## 增量按层拆解

### 1. Runtime Control Plane

新增或重点改动：

| file | 新增能力 |
|---|---|
| `agent_forge/runtime/execution_environment.py` | local/worktree 执行环境、network policy、git risk block、manifest。 |
| `agent_forge/runtime/hooks.py` | pre-tool approval、post-tool redaction、approval mode。 |
| `agent_forge/runtime/task_state.py` | task checkpoint、resume summary、trace replay。 |
| `agent_forge/runtime/agent_loop.py` | 接入 execution environment、hooks、task state、stop/failure 记录。 |
| `agent_forge/runtime/config.py` | 新增控制参数：timeout、budget、approval、task state、execution env。 |
| `agent_forge/cli.py` | 新增 CLI flags：review、worktree、approval mode、MCP config、task-state/replay。 |

你需要重点看：

```text
agent_forge/cli.py
agent_forge/runtime/agent_loop.py
agent_forge/runtime/execution_environment.py
agent_forge/runtime/hooks.py
agent_forge/runtime/task_state.py
```

这层的效果：

- 模型不再只是“想调用工具就调用工具”。
- 每个工具调用前后都有 policy/hook/trace。
- 可以解释 stop reason、失败恢复、权限拒绝、工作区隔离、长任务状态。

### 2. MCP And External Tools

新增：

| file | 新增能力 |
|---|---|
| `agent_forge/mcp/server.py` | 内置 stdio MCP-style JSON-RPC server。 |
| `agent_forge/mcp/builtin_server.py` | `python -m agent_forge.mcp.builtin_server` 启动入口。 |
| `agent_forge/mcp/web_tools.py` | `repo_policy`、`current_time`、`web_search`、`web_fetch`。 |
| `agent_forge/tools/mcp_stdio.py` | MCP stdio client，支持 `initialize`、`tools/list`、`tools/call`。 |
| `agent_forge/tools/mcp_config.py` | 从 JSON config 加载 MCP tools 到 `ToolRegistry`。 |
| `mcp_tools.example.json` | 默认 MCP server 配置和 allowlist。 |
| `scripts/verify_mcp.sh` | 离线验证 MCP server、discovery、tool call、registry registration。 |
| `tests/test_mcp_builtin_server.py` | MCP 回归测试。 |

这层的效果：

- Agent 能启动一个外部工具 server。
- 工具通过 schema discovery 注册，不必硬编码在 AgentLoop。
- `web_search` 默认 offline；需要联网时显式设置 provider 和 network flag。
- OpenAI / Claude hosted web search 被包到 MCP tool 后面，而不是污染主循环。

重点读：

```text
docs/study-pack/08-mcp-and-external-tools.md
agent_forge/mcp/server.py
agent_forge/mcp/web_tools.py
agent_forge/tools/mcp_config.py
agent_forge/tools/mcp_stdio.py
```

### 3. Review Gate And Quality Control

新增：

| file | 新增能力 |
|---|---|
| `agent_forge/workflows/review_workflow.py` | deterministic diff review gate。 |
| `agent_forge/cli.py` | `--mode review`。 |
| `tests/test_runtime_core_p0.py` | review risk、approval、manifest、MCP stdio 等 P0 tests。 |

效果：

- Agent 修完代码后，不只看 final answer。
- 可以通过 review workflow 检查 signature bypass、shell risk 等确定性风险。
- 技术讲解时可以说 Supervisor / Reviewer 不信模型文本，只信 artifact、diff、test、trace。

### 4. Observability, Usage, Eval

增强：

| file | 改动 |
|---|---|
| `agent_forge/observability/usage_report.py` | 增加 runtime control、hook、task status、context/tool breakdown。 |
| `agent_forge/observability/metrics.py` | 增加更多 trace metrics。 |
| `agent_forge/eval/eval_history.py` | eval regression history。 |
| `agent_forge/eval/eval_runner.py` | eval report 证据更完整。 |
| `agent_forge/eval/flywheel.py` | badcase flywheel。 |

效果：

- 可以看 per-step token、cost、latency、cache hit/miss。
- 可以看 context budget 消耗在哪。
- 可以看 tool success rate 和 failed observations。
- 可以跑 23 个 eval cases，而不是只展示一个 demo。

重点证据：

```text
docs/run-artifacts/webhook-deepseek/usage_report.md
docs/run-artifacts/webhook-deepseek/trace.json
docs/open-source-readiness/benchmark-summary.md
docs/open-source-readiness/provider-comparison.md
```

### 5. Open-Source Readiness

新增：

```text
LICENSE
CONTRIBUTING.md
SECURITY.md
RELEASE_CHECKLIST.md
.github/workflows/agent-forge-ci.yml
docs/assets/webhook-usage-report-snapshot.svg
docs/open-source-readiness/
docs/profile-drafting-brief.md
```

效果：

- 仓库不再只是本地学习项目，具备基本开源项目形态。
- README 首屏有 badge、架构图、运行证据截图。
- CI 跑 `scripts/verify.sh` 和 `scripts/verify_mcp.sh`。
- 有安全边界说明、贡献说明、发布 checklist。
- 有 benchmark、ablation、Docker sandbox extension、provider comparison 的基础材料。

### 6. Docs Consolidation

删除或合并：

```text
docs/study-pack/07-technical-answer-bank.md
docs/study-pack/08-runtime-call-chain-map.md
docs/study-pack/09-field-readiness-roadmap.md
docs/study-pack/10-technical-defense-playbook.md
```

替换成：

```text
docs/study-pack/07-schema-delta-guide.md
docs/study-pack/08-mcp-and-external-tools.md
docs/study-pack/09-project-profile.md
docs/study-pack/10-baseline-to-current-delta.md
docs/open-source-readiness/
docs/profile-drafting-brief.md
```

效果：

- 文档从“堆材料”变成“按任务读”。
- 旧的长问答和调用链说明被压缩进更结构化的 study-pack。
- 新增 open-source readiness 目录，专门回答公开项目成熟度。

## 当前应该按什么顺序看

如果你只想先理解 baseline 之后发生了什么：

1. `docs/study-pack/10-baseline-to-current-delta.md`
2. `docs/study-pack/07-schema-delta-guide.md`
3. `docs/study-pack/08-mcp-and-external-tools.md`
4. `docs/open-source-readiness/README.md`
5. `docs/study-pack/09-project-profile.md`
6. `docs/profile-drafting-brief.md`

如果你想回到代码：

1. `agent_forge/cli.py`
2. `agent_forge/runtime/agent_loop.py`
3. `agent_forge/runtime/execution_environment.py`
4. `agent_forge/runtime/hooks.py`
5. `agent_forge/runtime/task_state.py`
6. `agent_forge/tools/mcp_config.py`
7. `agent_forge/mcp/server.py`
8. `agent_forge/observability/usage_report.py`
9. `agent_forge/workflows/review_workflow.py`

## 当前状态判断

现在项目可以比较稳地描述为：

> A production-style CodingAgent runtime core, not a full IDE/cloud product.

已经有：

- 主 AgentLoop。
- Context engineering。
- Tool governance。
- Permission / sandbox / command policy。
- Runtime hooks and approval modes。
- Task checkpoint and trace replay。
- Worktree execution boundary。
- MCP server and external tool protocol。
- Review gate。
- Usage report and eval regression。
- WebhookPatchBench realistic fixture。
- Open-source project surface。

还没有：

- IDE / TUI 产品层。
- 真正容器级 sandbox backend。
- 远程 MCP gateway / OAuth / marketplace。
- 大规模线上 telemetry / SLA / 用户反馈闭环。
- 自动化 ablation runner。
- 多 provider 批量 benchmark runner。

## 怎么验证当前效果

本地无 API key：

```bash
scripts/verify.sh
scripts/verify_mcp.sh
```

个人 Mac 使用 DeepSeek：

```bash
local_scripts/run_webhook_deepseek.sh
```

读输出：

```text
.agent_forge/latest/webhook-deepseek/usage_report.md
.agent_forge/latest/webhook-deepseek/trace.json
docs/run-artifacts/webhook-deepseek/usage_report.md
docs/run-artifacts/webhook-deepseek/trace.json
```

## 这轮增量的结论

从 `codex/sourdaugh` 到 `schema`，核心变化不是“多了几个文档”。

真正的变化是项目从一个已经能讲 Agent 基础能力的 harness，升级成一个更完整的
runtime control plane reference：

- 对内：更可控、更可恢复、更可审计。
- 对外：更像一个能公开展示的开源项目。
- 对学习：可以先按增量读，再回到整体架构。
- 对技术说明：能明确区分已实现能力、边界、后续扩展，而不是把所有东西混在一起讲。

