# 可重复调试入口

这里仅保留一个需要逐步操作的 Lab：Durable Control。Multi-Agent 使用当前 Runtime
原生机制验证脚本，不再维护另一套教学场景或历史 schema。

## 共享 Run Configuration

| 配置 | 用途 |
| --- | --- |
| `NanoHarness Lab 1 - Governed Repair` | 在 TUI 中依次触发 Human Input、Approval、Resume、Pause/Cancel，并观察 durable JSON |
| `NanoHarness Evidence Workbench - Read Only` | 只读选择 Runtime、Multi-Agent 与 Benchmark evidence，不运行 Agent |
| `NanoHarness Review Preflight` | 校验 Workbench 可发现和可渲染的公开 evidence |

Lab 1 设有 7 个按 symbol 解析的条件断点；条件只在
`NANOHARNESS_DEBUG_LAB=governed` 时生效。

## 持久化控制（Durable Control）

运行：

```bash
.venv/bin/python examples/debug_lab/run.py governed --interactive --open-workbench
```

每次按钮操作后，直接检查本次不可变 Run 目录：

```text
.agent_forge/runs/showcases/<human-readable-run>/
├── human_input/
├── approvals/
├── operation_ledger/
└── phases/*/task_state/
```

这四类文件分别回答：缺少什么输入、写操作是否获准、副作用执行到哪、Run 当前停在哪。
Workbench 是只读投影，不拥有这些状态。

## Multi-Agent 当前机制验证

运行：

```bash
.venv/bin/python scripts/run_multi_agent_v1_smoke.py
```

脚本只固定模型响应和并发时序，实际经过：

```text
AdaptivePlanner
→ frozen FanoutPlan
→ FanoutCoordinator
→ isolated AgentLoop Worker Attempts
→ READY / FEEDBACK / UPDATE
→ strict integration frontier
→ read-only Finalizer
```

版本化结果写入：

```text
benchmarks/experiments/multi-agent-v1/mechanism-evidence.json
```

原始本机 Trace 写入 `.agent_forge/runs/v1-multi-agent/`，不纳入 Git。

## 只读 Workbench

```bash
.venv/bin/python examples/debug_lab/run.py workbench
```

Workbench 只发现已有 artifact；切换页面不会启动 Agent、修改 Run 或提升证据等级。
