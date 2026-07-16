# `agent_forge` 包内导航

本文件只帮助从 package 目录定位代码。项目定位和命令见根目录 `README.md`，架构规则
见 `docs/ARCHITECTURE.md`，能力状态见 `docs/CAPABILITY_REALITY_MATRIX.md`。

## 先看入口

```text
__main__.py
-> forge_cli.py                 打包工具要求的最小 main 入口
-> cli/parser.py                参数契约
-> cli/dispatch.py              命令分发
-> cli/repository.py            run 装配
-> capability api.py/wiring.py
```

## Capability 地图

| Package | 第一入口 | 主要责任 |
| --- | --- | --- |
| `runtime` | `application/agent_loop.py` | 单 Agent 控制循环、HITL、审批、恢复、幂等 |
| `multi_agent` | `application/coordinator.py`、`live_fanout.py` | 顺序角色和并发 DAG |
| `bench` | `application/swebench.py` | case 执行、official eval 时序、诊断和发布 |
| `evaluation` | `api.py` | comparison、scorecard、ablation、feedback data |
| `observability` | `application/usage.py` | trace 事实到 read model |
| `workbench` | `api.py` | 本地 evidence console 与受限命令 |
| `context` | `context_builder.py` | repo/context selection 与预算 |
| `tools` | `registry.py`、`tool_router.py` | 工具可见性、schema、执行 |
| `safety` | `permission.py`、`command_policy.py`、`sandbox.py` | 确定性安全边界 |
| `models` | `gateway.py` | provider retry/fallback/usage |
| `skills` | `registry.py` | versioned Skill selection |
| `mcp` | `server.py` | 精简 stdio MCP surface |

## Runtime 最短阅读顺序

1. `runtime/application/agent_loop.py`：只展开 `AgentLoop.run`。
2. `runtime/application/session.py`：把 `AgentRunSession` 当字段表。
3. `runtime/application/run_preparation.py`：看 `start/execute`。
4. `runtime/application/turn_preparation.py`：看 context 和 tool routing。
5. `runtime/application/tool_execution.py`：看两个入口。
6. 根据场景选择 `tool_authorization.py`、`operation_tracker.py`、
   `tool_feedback.py` 或 `run_lifecycle.py`。
7. 只有需要存储细节时才进入 `runtime/adapters/`。

## 分层约定

```text
api.py -> application -> domain + ports
wiring.py -> application + adapters
adapters -> ports
presentation -> API/read model
```

旧路径 facade 已删除。外围代码从对应 capability 的 `api.py` 进入；能力包内部使用明确的
Domain、Application、Port、Adapter 或 Presentation 路径。
