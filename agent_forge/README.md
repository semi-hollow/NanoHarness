# `agent_forge` 包内导航

本文件只帮助从 package 目录定位代码。项目定位和公开命令见根目录 `README.md`，硬约束见
`docs/ARCHITECTURE.md`；训练与闭卷问题见
[NanoHarness Study Notes](https://github.com/semi-hollow/NanoHarness-Study-Notes)。

## 唯一 Single-Run 入口

```text
__main__.py / forge_cli.py
-> cli/parser.py                 参数契约
-> cli/dispatch.py               薄命令分发
-> cli/repository.py             类型化配置与 Adapter 选择
-> harness.py::Harness.run       唯一 Single-Run Public API
-> runtime/wiring.py
-> runtime/application/agent_loop.py
-> RunResult + RunManifest / RunStory
```

Single mode 中，`cli/repository.py` 不拥有第二套 trace、environment、AgentLoop、patch 或 cleanup。
Multi/Fanout 保留为 Advanced coordinator，不属于这条黄金主链。

## 12 文件核心阅读面

1. `harness.py`：Public request/result 与 run 边界。
2. `runtime/wiring.py`：唯一 Runtime composition owner。
3. `runtime/application/agent_loop.py`：阶段编排。
4. `runtime/application/session.py`：进程内 run state。
5. `runtime/application/turn_preparation.py`：Context/Tool schema -> Model。
6. `runtime/application/tool_execution.py`：确定性工具治理与执行。
7. `runtime/application/operation_tracker.py`：identity、approval、stale、ledger。
8. `runtime/application/run_lifecycle.py`：checkpoint、HITL、stop。
9. `runtime/domain/task.py`：durable task state。
10. `runtime/domain/operation.py`：副作用状态机。
11. `observability/domain/event.py`：运行事实。
12. `observability/domain/run_story.py`：artifact 血缘与 canonical Read Model。

第一遍不要进入 CLI parser、Adapter 序列化、Memory、MCP、Skills、Multi/Fanout、Campaign 或 UI。

## Capability 地图

| Package | 第一入口 | 主要责任 |
| --- | --- | --- |
| `runtime` | `application/agent_loop.py` | 单 Agent 控制循环、工具治理、HITL、恢复、幂等 |
| `observability` | `domain/run_story.py` | trace facts、artifact manifest 与 Run Story |
| `context` | `context_builder.py` | repository/context selection 与预算 |
| `tools` / `safety` | `registry.py` / policy modules | 工具 schema、权限、命令和路径边界 |
| 高级：`bench` / `evaluation` | `api.py` | 评测用例、官方判定、计分卡与重复实验 |
| Advanced：`multi_agent` | `api.py` | 顺序角色与 live fanout |
| Advanced：`workbench` | `api.py` | 只读 Evidence presentation |
| Advanced：Context Memory / `skills` / `mcp` | `context/api.py` / `skills/__init__.py` / `mcp/server.py` | 可选 Context 与工具集成 |

## 导航契约

```bash
forge inspect AgentLoop.run
forge inspect ToolExecutionPipeline.execute_calls
forge inspect <run-or-artifact>
```

随机 symbol 必须能说明层级、规范上游、下一 owner、状态/副作用、Evidence 和删除影响；随机
artifact 必须能说明 producer、consumer、source/authority、claim boundary 与可重建性。Code
Compass 的静态 caller/callee 不等于完整运行时调用图，动态注入边以 Core owner 契约为准。

## 分层约定

```text
api.py -> application -> domain + ports
wiring.py -> application + adapters
adapters -> ports
presentation -> API / canonical read model
```

Port 只为真实外部边界、替换需求或有价值的测试替身存在。无新增语义的 Wrapper、Service、Mapper
和单实现 Port 不能仅因“六边形架构”保留。
