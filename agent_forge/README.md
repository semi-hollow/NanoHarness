# agent_forge 代码地图

当你想知道“一个 package 为什么存在、删掉后会破坏什么”时，阅读这份文档。

## 主调用链

```text
agent_forge/forge_cli.py
  -> ui.py：`forge ui` 本地浏览器工作台
  -> bench/swebench.py：`forge bench swebench`
  -> runtime/agent_loop.py：Agent 执行
  -> context/*：构造 prompt context
  -> models/gateway.py：调用 provider
  -> tools/*：执行受控 action
  -> safety/*：强制边界
  -> observability/*：写 trace 和 usage
  -> evaluation/*：写 scorecard 和 paired ablation
```

公共入口是 `forge` / `python -m agent_forge`。旧的 mode-based entrypoint 已删除，
确保代码地图与 benchmark loop 对齐。

## Package 职责

| Package | 作用 | 删除后会怎样 |
| --- | --- | --- |
| `bench/` | 加载 SWE-bench case、准备 repo workspace、写 prediction 和 result card。 | 项目失去外部效果闭环，只剩主观案例。 |
| `runtime/` | 管理 AgentLoop、task state、control policy、planning mode 和 observation。 | Tool call 四散，replay/recovery 无法统一。 |
| `context/` | 选择 repo file、retrieved doc、symbol、memory 和 token budget。 | 模型要么收到噪声 full-repo context，要么遗漏必要文件。 |
| `tools/` | 提供 read、grep、patch、command、git、diagnostics 和 MCP-style adapter。 | 模型无法通过受治理 action 检查和修改代码。 |
| `safety/` | Path sandbox、command policy、permission 和 guardrail。 | Coding Agent 可能执行危险或无关操作。 |
| `models/` | Provider gateway、retry/fallback、token/cache/cost telemetry。 | Runtime 与单一 provider 耦合，失去成本可见性。 |
| `observability/` | Trace、metric、evidence 和 usage report。 | 无法解释 Agent 为什么选文件、工具为什么失败、token 花在哪里。 |
| `evaluation/` | Run scorecard、matched ablation、mini-case、human feedback 和安全 dataset projection。 | Runtime change 失去量化比较和 evidence denominator。 |
| `mcp/` | 内置 MCP-style stdio tool 和 external web tool wrapper。 | External tool integration 消失，但 SWE-bench patching 仍可运行。 |
| `skills/` | 内置 Coding Skill 和 versioned custom manifest；active Skill 向 AgentLoop 注入 procedure 和 expected tool。 | Tool capability 无法提升为受治理 workflow，也无法安全 rollback。 |
| `ui.py` | 本地浏览器控制面，支持 doctor、agent run、SWE-bench reference case、report、replay。 | 用户必须先记住 CLI 才能看完整闭环。 |

## 重要类型

| 类型 | 为什么存在 |
| --- | --- |
| `BenchCase` | 标准化 SWE-bench row，使 runner 不绑定某一种 dataset schema。 |
| `SwebenchWorkspaceManager` | 保证每个 case 从 official base commit 开始。 |
| `BenchRunSummary` | 为 `results.json` 和 `report.md` 提供同一 source of truth。 |
| `AgentLoop` | 编排 context、model、tool call、observation、recovery 和 stop reason。 |
| `ContextBuildReport` | 让 prompt assembly 可审计，而不是 opaque string。 |
| `ModelGateway` | 标准化 DeepSeek/OpenAI-compatible provider response 和 usage。 |
| `ToolRegistry` | Agent 可以请求的 action 的唯一 registry。 |
| `ToolRouter` | 将大 tool catalog 收敛为相关工具，并记录其他工具为何隐藏。 |
| `SkillRegistry` | 选择具体 Coding Skill，跟踪 version、owner、permission、dependency 和 rollback target。 |
| `StructuredOutputParser` | 校验 model JSON output、构造确定性 repair prompt、保护 tool-call argument parsing。 |
| `WorkspaceSandbox` | 防止 tool 逃逸 target repo。 |
| `CommandPolicy` | 阻断危险 shell command，并解释允许的 validation command。 |
| `ExecutionEnvironment` | 选择 local/worktree/OCI execution，记录 image/resource/command evidence。 |
| `TraceRecorder` | 写入逐 step evidence stream。 |
| `UiState` | 保存一次本地工作台 session 中由浏览器触发的 job 和 output。 |

## 阅读顺序

1. `agent_forge/forge_cli.py`
2. `agent_forge/ui.py`
3. `agent_forge/bench/swebench.py`
4. `agent_forge/runtime/agent_loop.py`
5. `agent_forge/runtime/execution_environment.py`
6. `agent_forge/context/context_builder.py`
7. `agent_forge/tools/registry.py`
8. `agent_forge/safety/command_policy.py`
9. `agent_forge/bench/official_results.py`
10. `agent_forge/evaluation/scorecard.py`
11. `agent_forge/evaluation/experiment.py`
12. `agent_forge/observability/usage_report.py`
