# `agent_forge` Core 导航

`agent_forge/` 是可复用 Core，不包含 CLI、Operator Console、Showcase、Workbench 或 MCP Server。
这些入站应用统一位于顶层 `apps/`，依赖方向只能是：

```text
apps
  ↓
agent_forge public/capability API
```

`agent_forge/__main__.py` 仅为保留 `python -m agent_forge` 的 executable bootstrap 例外。

## Single-Agent 主链

```text
apps/repository_run.py
→ Harness.run                         agent_forge/harness.py
→ build_agent_loop_from_request       agent_forge/runtime/wiring.py
→ AgentLoop.run                       agent_forge/runtime/application/agent_loop.py
→ RunResult + RunManifest / RunStory
```

`Harness.run` 是 standalone/public Single-Agent fresh-run facade；`Harness.resume` 是公开 continuation
facade。Bench Worker、Multi-Agent Worker 与 Finalizer 可直接使用 `runtime.wiring` 装配，但最终都进入
同一个 `AgentLoop.run` Runtime Kernel。

## 能力包怎么读

```text
application/  用例编排与控制流
domain/       业务状态与纯语义
ports/        Core 需要的外部能力契约
adapters/     文件、Git、Provider、进程等具体实现
api.py        能力对外入口
wiring.py     该能力的装配点
```

Domain 不等于“只有字段的类”；真正决定状态是否合法、如何转移的纯业务规则也属于 Domain。
Application 负责何时调用这些规则，不拥有 Provider、文件或进程 IO。

## Capability 第一入口

| Capability | 第一处代码 |
| --- | --- |
| Runtime 主循环 | `runtime/application/agent_loop.py` |
| Context 组装 | `context/application/context_builder.py` |
| Conversation Compaction | `context/application/compaction.py` |
| Long-Term Memory | `memory/application/service.py` |
| Tool Governance | `runtime/application/tool_execution.py` |
| Tool 实现 | `tools/builtins/` |
| Model Gateway | `runtime/adapters/model_gateway.py` |
| Multi-Agent | `multi_agent/api.py` |
| Benchmark | `bench/api.py` |
| Evaluation | `evaluation/api.py` |
| Evidence | `observability/api.py` |

代码讲解或审阅时先打开 [`docs/核心能力与代码入口.md`](../docs/核心能力与代码入口.md) 指定的唯一
Owner，再用 IDE 的 Go to Declaration / Implementation / Usages 追相邻 Port 和 Adapter；不要从展示层、
测试或全项目文本搜索反推主链。
