# `agent_forge` 包内导航

本文件只帮助从 package 目录定位代码。项目定位和公开命令见根目录 `README.md`，
架构约束见 `docs/项目架构与代码导航.md`，机制语义见 `docs/运行生命周期与异常处理机制.md`。

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

## Single-Run 核心入口

1. `harness.py::Harness.run`：一次 Run 从哪里进入和返回。
2. `runtime/application/agent_loop.py::AgentLoop.run`：模型循环怎样推进和停止。
3. `runtime/application/turn_preparation.py::TurnPreparation.prepare_turn`：模型本轮看到什么。
4. `runtime/application/tool_execution.py::ToolExecutionPipeline.execute_calls`：ToolCall 怎样被治理和执行。
5. `runtime/application/run_lifecycle.py::RunLifecycle.finalize_run`：等待、恢复和终态怎样落盘。

CLI parser、Adapter 序列化、Memory、MCP、Skills、Multi/Fanout、Campaign 和 UI 属于扩展实现，
不在 Single-Run 核心路径内。具体机制可按下面四层定位，无需沿 import 关系遍历全部源码。

## ToolCall 四层代码地图

| 主视图 | 核心问题 | 主要入口 | 深入实现 |
| --- | --- | --- | --- |
| 入口控制 | 调用是否合法、当前是否可用 | `turn_preparation.py`、`tool_execution.py` | `tools/tool_router.py`、`models/tool_call_normalizer.py`、`tools/registry.py` |
| 执行决策 | 允许、拒绝、询问人工，还是已有结果 | `tool_execution.py::_execute_call` | `operation_tracker.py`、`tool_authorization.py`、`runtime/hooks.py` |
| 受限执行 | 获准后最多能影响哪里 | `tools/<tool>.py::execute` | `safety/command_policy.py`、`safety/sandbox.py`、`runtime/execution_environment.py` |
| 结果与恢复 | 发生了什么，中断后怎样继续 | `tool_execution.py::_run_tool` | `run_lifecycle.py`、`domain/operation.py`、`observability/domain/event.py` |

本表只给代码路径；四层、Hook 和操作状态表的语义由
`docs/运行生命周期与异常处理机制.md` 统一说明。

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

随机 symbol 必须能说明层级、规范上游、下一 owner、状态变更、Evidence 和删除影响；随机
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
