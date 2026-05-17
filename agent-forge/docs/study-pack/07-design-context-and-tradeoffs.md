# 07 设计背景与取舍说明

这份文档专门写“代码里不明显，但你必须知道”的背景。它的目的不是包装项目，而是减少你读代码时的无效猜测。

## 先说总原则

Agent Forge 是面试用的 production-oriented coding-agent runtime，不是完整 OpenCode 产品。

它刻意把几个概念拆开：

```text
single   -> 完整 AgentLoop
multi    -> runtime-backed supervisor / handoff / retry / review gate
workflow -> deterministic workflow baseline
```

所以你看到某些地方“写死”“简单”“不够工业级”，通常不是因为这个方向不重要，而是因为当前版本先把核心概念拆成能跑、能测、能讲清楚的小切片。

## 为什么 single 直接走 AgentLoop，multi 通过 AgentRuntime 复用 AgentLoop

当前入口：

```text
single   -> AgentLoop
multi    -> SupervisorAgent -> TaskGraph -> AgentRuntime -> AgentLoop
workflow -> run_workflow
```

原因：

- `single` 用来展示完整自治 agent 的闭环：context、LLM、tool、permission、observation、trace。
- `multi` 用来展示 runtime-backed supervisor 编排：TaskGraph、role spec、handoff、失败重试、review gate。
- `workflow` 用来展示确定性流程和 agent loop 的差异。

如果一开始就让 `multi` 也套多个 `AgentLoop`，初学时会把两个问题混在一起：

```text
问题 A：一个 agent 怎样安全地执行工具？
问题 B：多个 agent 怎样被调度和协作？
```

当前项目已经把 A 和 B 接起来：`single` 展示单 runtime，`multi` 展示多个 runtime-backed workers 被 supervisor 调度。

## 这是不是说明 multi-agent 不需要 AgentLoop

不是。

当前 multi-agent 已经采用这个结构的最小实现：

```text
Supervisor / Orchestrator
  -> Planner worker  -> AgentLoop(role=planner)
  -> Coding worker   -> AgentLoop(role=coder)
  -> Testing worker  -> AgentLoop(role=tester)
  -> Review worker   -> AgentLoop(role=reviewer)
```

也就是说，`AgentLoop` 应该成为通用 agent runtime。`SupervisorAgent` 不直接写业务动作，而是调度多个 runtime-backed workers。

## 当前 multi mode 到底是什么

当前 `multi` 是 conflict-aware task graph：

```text
TaskGraph
  -> AgentRuntime(PlannerAgent)
  -> AgentRuntime(CodingAgent)
  -> AgentRuntime(TesterAgent)
  -> AgentRuntime(CodingAgent retry)
  -> AgentRuntime(TesterAgent retest)
  -> AgentRuntime(ReviewerAgent)
```

它展示了：

- supervisor 控制流程；
- 每次 handoff 都有 trace；
- 测试失败可以回到 coding；
- review 作为最后 gate；
- 多角色不是自由互聊，而是被调度。

它没有展示：

- 更强的动态任务拆分；
- 更复杂的自动 patch merge；
- 成本、延迟、重试预算；
- 高风险任务升级人工审批。

## 为什么 subagent 这么简单

`PlannerAgent/CodingAgent/TesterAgent/ReviewerAgent` 当前由 `AgentSpec` 描述，并通过 `AgentRuntime` 执行。

它们简单，是为了让你先看到角色边界：

| 角色 | 当前做什么 | 生产级会多什么 |
| --- | --- | --- |
| Planner | 写一个固定 plan | 任务拆分、依赖关系、验收标准、风险评估 |
| Coding | 修 demo bug | 代码定位、patch 生成、文件 ownership、局部验证 |
| Tester | 跑固定 unittest | 测试选择、失败归因、flaky 处理、覆盖率 |
| Reviewer | 看测试和 diff | 语义风险、安全、兼容性、缺失测试、风格规范 |

## 为什么 MockLLM 看起来像脚本

`MockLLMClient` 的目的不是证明模型聪明，而是证明 runtime 可控。

它让这些东西稳定可测：

- tool call 能被解析；
- patch 失败会变成 observation；
- observation 会回到下一轮；
- agent 可以 recovery；
- 最终答案前会检查是否跑过测试；
- trace 每次都能复现。

真实 LLM 会增加不确定性。测试 runtime 时，确定性比“像不像真的模型”更重要。

## 为什么 workflow mode 几乎什么都没做

`workflow` 是对照组。

它回答的问题是：

```text
如果流程完全确定，还需要 agent 吗？
```

答案通常是不需要。固定流程用普通 workflow 更稳、更便宜、更好测。

所以项目保留 workflow mode，是为了让你在面试里能说清楚：

> 我不是所有问题都上 agent。稳定、可枚举的流程用 workflow；需要根据 observation 动态决策、使用工具、处理失败恢复时才引入 agent loop。

## 当前项目最值得讲的不是“多高级”

最值得讲的是工程边界：

```text
LLM 不直接执行工具
工具统一走 ToolRegistry
执行前过 guardrail 和 permission
文件访问受 WorkspaceSandbox 限制
命令执行受 CommandPolicy 限制
工具结果变成 Observation
每一步写 Trace
eval_cases 可执行验证
```

这些比“我有很多 agent”更像工程项目。

## 面试时遇到质疑怎么答

### 质疑 1：multi-agent 是不是太简单了？

答：

> 当前 multi-agent 是 runtime-backed DAG。每个 subagent 都通过 AgentRuntime 复用 AgentLoop，由 AgentSpec 限制工具权限和文件读写范围；TaskScheduler 支持 conflict-aware parallel batches；OwnershipPlan 和 TaskArtifact 分别解决文件所有权和产物交接。

### 质疑 2：为什么 plan -> code -> test -> review 写死？

答：

> 因为这是最小可解释链路。写死能让 trace 和代码一一对应，方便验证 supervisor 控制流。生产级会改成任务 DAG，由 supervisor 根据依赖、风险和资源动态调度。

### 质疑 3：有没有并发和冲突处理？

答：

> 有。TaskScheduler 会把 ready nodes 分成 conflict-safe batches，没有写冲突的 batch 可以并发执行；如果多个 worker 要写同一个文件，会拆到不同 batch，避免覆盖。这个 demo 输出像顺序，是因为 plan、code、test、review 本身有强依赖。

### 质疑 4：MockLLM 是不是没意义？

答：

> MockLLM 的意义是稳定测试 runtime 控制层，不是替代真实模型。真实模型路径通过 OpenAI-compatible client 保留；单测和 eval 用 MockLLM 保证回归稳定。

### 质疑 5：这离生产还差什么？

答：

> 作为 runtime 核心已经具备：AgentLoop、AgentRuntime、TaskGraph、OwnershipPlan、TaskArtifact、ModelGateway、SessionStore、DiffTracker、DiagnosticsTool、EvalHistory。完整产品还差 IDE/TUI、容器级 sandbox、完整 LSP、真实 provider pricing 和 MCP marketplace。

## 你看代码时的判断方式

看到一段代码，先问：

```text
它是在展示核心机制，还是在模拟生产能力？
```

对应关系：

| 文件 | 判断 |
| --- | --- |
| `runtime/agent_loop.py` | 核心机制，认真看 |
| `tools/` | 核心机制，认真看 |
| `safety/` | 核心机制，认真看 |
| `observability/trace.py` | 核心机制，认真看 |
| `agents/supervisor_agent.py` | runtime-backed multi-agent 编排 |
| `runtime/agent_spec.py` | subagent 的角色、prompt、工具权限、步数预算 |
| `runtime/agent_runtime.py` | subagent 复用 AgentLoop 的执行层 |
| `workflows/coding_workflow.py` | workflow 对照组 |

这样读，你不会把教学 stub 当成生产实现，也不会低估核心 runtime 的工程价值。
