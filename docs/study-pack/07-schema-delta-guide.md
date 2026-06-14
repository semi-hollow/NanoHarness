# 07 Schema Branch Delta Guide

这份文件只讲 `codex/sourdaugh -> schema` 的增量。你已经理解旧版本时，从这里开始读。

## 一句话变化

`sourdaugh` 已经有 Context、AgentLoop、Tool、Safety、Trace、Usage、Eval 的主骨架。
`schema` 把它补成更完整的 runtime control plane：执行环境、approval mode、hooks、task state、
内置 MCP server、MCP stdio 工具、hosted web search wrapper、review gate、eval regression、
FORGE.md 项目规则。

## 新增核心文件

| file | 新增能力 | 先看什么 |
|---|---|---|
| `FORGE.md` | 仓库级运行规则，被 ContextBuilder 注入 prompt。 | command、editing、runtime architecture rules。 |
| `agent_forge/runtime/execution_environment.py` | local/worktree、network deny、protected paths、git risk block、manifest。 | `ExecutionEnvironmentConfig`, `prepare()`, `validate_command()`, `write_manifest()`。 |
| `agent_forge/runtime/hooks.py` | pre-tool approval、post-tool redaction、approval mode。 | `ApprovalMode`, `PermissionHook`, `ExecutionEnvironmentHook`, `HookManager.pre_tool()`。 |
| `agent_forge/runtime/task_state.py` | checkpoint、resume summary、trace replay。 | `TaskCheckpoint`, `TaskStateStore`, `replay_trace()`。 |
| `agent_forge/tools/mcp_config.py` | config-driven external tools。 | `load_into()`, `_register_stdio_server()`。 |
| `agent_forge/tools/mcp_stdio.py` | stdio JSON-RPC server discovery/call。 | `discover_tools()`, `call_tool()`, `MCPStdioTool.execute()`。 |
| `agent_forge/mcp/server.py` | 项目内置 MCP server 的 JSON-RPC 协议壳。 | `AgentForgeMCPServer`, `MCPToolDefinition`, `MCPToolResult`。 |
| `agent_forge/mcp/web_tools.py` | MCP 工具实现：repo policy、time、web_fetch、web_search。 | `build_builtin_tools()`, `_web_search()`。 |
| `agent_forge/mcp/builtin_server.py` | MCP server 命令入口。 | `--list-tools`, `--call`, 默认 stdio server。 |
| `agent_forge/workflows/review_workflow.py` | deterministic diff review gate。 | `run_review()`, `_analyze_diff()`。 |
| `tests/test_runtime_core_p0.py` | P0 smoke/regression tests。 | approval、manifest、MCP stdio、review risk。 |
| `tests/test_mcp_builtin_server.py` | 内置 MCP server 回归测试。 | discovery、offline web_search、example config registration。 |

## 修改过的关键旧文件

| file | 改了什么 |
|---|---|
| `agent_forge/cli.py` | 新增 `--mode review`、`--execution-env`、`--approval-mode`、task-state、MCP config、manifest 写入。 |
| `agent_forge/runtime/agent_loop.py` | 工具执行前后接 HookManager；每轮写 TaskState；stop 时写 stop hooks。 |
| `agent_forge/context/context_builder.py` | 加载 `FORGE.md` 到 `project_instructions`。 |
| `agent_forge/tools/tool_router.py` | 支持 external/MCP-style tool routing。 |
| `agent_forge/eval/eval_history.py` | 增加 previous-run regression compare。 |
| `agent_forge/observability/usage_report.py` | 增加 Runtime Control、hook decisions、task status。 |

## 新命令

```bash
# 1. review gate
python run_demo.py --mode review

# 2. worktree 隔离执行
python run_demo.py --mode single --execution-env worktree

# 3. approval mode
python run_demo.py --mode single --approval-mode on-risk
python run_demo.py --mode single --approval-mode dry-run

# 4. task state
python run_demo.py --list-task-states
python run_demo.py --show-task-state <run_id>
python run_demo.py --resume-state <run_id> --mode single

# 5. trace replay
python run_demo.py --replay-run .agent_forge/latest/webhook-deepseek/trace.json

# 6. MCP-style config through built-in stdio server
scripts/verify_mcp.sh
python -m agent_forge.mcp.builtin_server --workspace . --list-tools
python run_demo.py \
  --mcp-config mcp_tools.example.json \
  --mcp-allowed-tool forge.repo_policy \
  "use the repo_policy tool to summarize command policy"

# 7. Optional live web lookup through MCP
AGENT_FORGE_MCP_ALLOW_NETWORK=1 \
AGENT_FORGE_WEB_PROVIDER=duckduckgo \
python run_demo.py --mcp-config mcp_tools.example.json \
  "search the web for current public MCP tooling examples"
```

## 读新代码顺序

1. `FORGE.md`：先知道 runtime 给模型的项目规则。
2. `agent_forge/cli.py`：看新参数如何接入。
3. `execution_environment.py`：看隔离和命令边界。
4. `hooks.py`：看 approval mode 如何变成 allow/ask/deny。
5. `agent_loop.py`：搜索 `hook_check` 和 `task_state_checkpoint`。
6. `mcp_config.py` + `mcp_stdio.py`：看外部工具客户端协议。
7. `agent_forge/mcp/server.py` + `web_tools.py`：看内置 MCP server 和常用外部工具。
8. `review_workflow.py`：看质量门禁。
9. `usage_report.py`：看新证据如何出现在报告里。

## 现在能回答的新问题

| question | schema 分支回答 |
|---|---|
| 生产里怎么防止 agent 污染本地仓库？ | `--execution-env worktree` 创建独立 git worktree；session 记录 active workspace、commit、dirty files。 |
| 审批策略怎么做？ | `ApprovalMode` 支持 trusted/on-write/on-risk/locked/dry-run，HookManager 在工具执行前统一决策。 |
| 外部工具怎么接？ | MCP config 先 allowlist，再 discovery/register；stdio server 通过 `tools/list` 和 `tools/call` 接入 ToolRegistry。schema 分支还内置了 `forge.*` MCP server，能验证 repo policy、time、web_search、web_fetch。 |
| 为什么 web search 不直接写进 AgentLoop？ | web search 是 provider/tool capability，MCP 是协议边界。把 OpenAI/Claude/DuckDuckGo 都封到 MCP tool 后面，AgentLoop 只处理统一 schema、权限、observation、trace。 |
| 长任务怎么恢复？ | TaskState 保存 last_tool/last_observation/resume_hint；`--resume-state` 把它作为 context seed。 |
| 怎么做代码审查？ | `--mode review` 读取 diff，确定风险 finding 和 verdict，写 trace/usage。 |
| 怎么证明没有回归？ | eval report 记录 pass_rate_delta、新增失败、修复失败。 |

## 还没有塞进仓库的边界

这些不属于 schema P0 的 runtime core：

- IDE/TUI 交互。
- 云端容器调度平台。
- GitHub PR 评论机器人。
- 多模态生成流水线。
- 模型训练/SFT/RL。
- 大规模企业知识库/GraphRAG 平台。

这些可以作为扩展方向讲，但不要把它们混进当前代码主线。
