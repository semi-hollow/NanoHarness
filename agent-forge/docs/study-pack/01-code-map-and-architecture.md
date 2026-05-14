# 01 代码地图与整体架构

## 一句话定位

Agent Forge 是一个 coding agent harness。它的重点不是模型本身，而是把 LLM 变成“可控执行系统”的工程层：上下文、工具、安全、循环、trace、eval。

## 总体架构图

```mermaid
flowchart TD
    User["User task / CLI"] --> CLI["run_demo.py / agent_forge.cli"]
    CLI --> Mode{"mode"}
    Mode -->|single| Loop["AgentLoop"]
    Mode -->|multi| Supervisor["SupervisorAgent"]
    Mode -->|workflow| Workflow["Deterministic Workflow"]

    Loop --> Context["Context Builder"]
    Context --> RepoMap["repo_map"]
    Context --> Memory["memory"]
    Context --> RAG["keyword RAG"]
    Context --> Symbol["symbol_search"]
    Context --> Ranker["file_ranker"]

    Loop --> LLM["MockLLM / OpenAI-compatible LLM"]
    LLM --> ToolCall["ToolCall"]
    ToolCall --> Guardrail["tool guardrail"]
    Guardrail --> Permission["PermissionPolicy"]
    Permission --> Registry["ToolRegistry"]
    Registry --> Tools["read / write / grep / patch / run / git / ask_human"]
    Tools --> Sandbox["WorkspaceSandbox / CommandPolicy"]
    Tools --> Observation["Observation"]
    Observation --> Loop

    Loop --> Trace["Trace JSON"]
    Supervisor --> Trace
    Trace --> Metrics["metrics"]
    Metrics --> Eval["eval_report.md"]
```

## 目录分布

```text
agent-forge/
  run_demo.py
  agent_forge/
    cli.py
    runtime/
    tools/
    safety/
    context/
    agents/
    workflows/
    observability/
    eval/
    production/
  tests/
  eval_cases/
  examples/demo_repo/
  scripts/
  local_scripts/
  docs/
  tutorials/
```

## 每块代码负责什么

| 路径 | 角色 | 你该怎么理解 |
| --- | --- | --- |
| `run_demo.py` | 最薄入口 | 只负责调用 `agent_forge.cli.main()`。面试时说它是 CLI entrypoint。 |
| `agent_forge/cli.py` | 模式分发和配置入口 | 解析参数，选择 single/multi/workflow，组装 registry、trace、LLM。 |
| `agent_forge/runtime/agent_loop.py` | 单 Agent 主循环 | 最核心文件。完成 guardrail -> context -> LLM -> tool -> observation -> final。 |
| `agent_forge/runtime/llm_client.py` | LLM 客户端 | MockLLM 和 OpenAI-compatible API 都在这里。 |
| `agent_forge/runtime/llm_config.py` | LLM 配置解析 | 让模型可以从 CLI/env/profile 平滑切换。 |
| `agent_forge/tools/registry.py` | 工具路由表 | 注册工具、暴露 schema、按名字执行工具、处理 unknown tool。 |
| `agent_forge/tools/*.py` | 具体工具 | 读文件、写文件、patch、grep、跑命令、git status/diff。 |
| `agent_forge/safety/*.py` | 安全边界 | sandbox、权限、命令策略、输入输出 guardrail。 |
| `agent_forge/context/*.py` | 上下文工程 | repo map、检索、记忆、symbol search、文件排序、预算报告。 |
| `agent_forge/agents/*.py` | 多 Agent 角色 | Supervisor、Planner、Coding、Tester、Reviewer。 |
| `agent_forge/workflows/*.py` | 固定 workflow | 不依赖 LLM 的确定性流程，用来对比 agent。 |
| `agent_forge/observability/*.py` | 可观测性 | trace、metrics、summary。 |
| `agent_forge/eval/*.py` | 评测执行 | 扫 eval_cases，真实运行每个 verify.py。 |
| `tests/` | 单元测试 | 验证各模块行为。 |
| `eval_cases/` | 行为回归用例 | 每个 case 有 task 和 verify，证明项目能力不是口头说的。 |

## 依赖关系怎么读

最重要的依赖方向是单向的：

```text
cli
  -> runtime
  -> context / safety / tools / observability

agents
  -> tools / trace

eval
  -> run_demo.py / verify.py
```

你面试时可以强调：`AgentLoop` 不直接知道每个工具的细节，它只通过 `ToolRegistry` 执行工具；这让工具层可扩展，也让 loop 的职责保持清楚。

## 三条执行路径

### single

```text
run_demo.py
  -> cli.main
  -> AgentLoop
  -> MockLLM or OpenAI-compatible LLM
  -> ToolRegistry
  -> Observation
  -> Trace
```

这是最适合读 agent 基础架构的路径。

### multi

```text
run_demo.py
  -> cli.main
  -> SupervisorAgent
  -> PlannerAgent
  -> CodingAgent
  -> TesterAgent
  -> ReviewerAgent
  -> Trace
```

这是用来讲 supervisor/subagent 编排的路径。

### workflow

```text
run_demo.py
  -> cli.main
  -> run_workflow
  -> WorkflowState
```

这是用来讲 deterministic workflow 和 agent loop 差异的路径。

## 你看到一个文件时怎么定位

如果文件名在 `runtime/`，先问：它是不是控制 loop、状态、模型响应、停止条件？

如果文件名在 `tools/`，先问：它是不是一个可被 LLM 调用的动作？

如果文件名在 `safety/`，先问：它是在执行前拦截风险，还是执行后检查声明？

如果文件名在 `context/`，先问：它是不是帮 LLM 决定“应该看哪些信息”？

如果文件名在 `observability/`，先问：它是不是把运行过程变成可审计证据？

如果文件名在 `eval/` 或 `eval_cases/`，先问：它是不是证明某个能力真的跑通？
