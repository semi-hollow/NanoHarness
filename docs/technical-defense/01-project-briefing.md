# 01 项目答辩讲法

这份文件解决“我怎么把项目讲清楚”的问题。不要从“我写了很多模块”开始讲，
要从问题、架构、主链路、证据、边界五层讲。

## 30 秒版本

Agent Forge 是一个 production-style CodingAgent runtime core。它不是 UI 产品，
也不是模型训练项目，而是把 LLM 接入受控代码执行系统需要的控制面做出来：
上下文工程、模型网关、工具治理、执行环境、权限审批、任务状态、trace、usage、
MCP 外部工具、eval 和 review gate。

我用 WebhookPatchBench 作为主验证场景，让 agent 修一个 webhook 服务里的幂等 bug。
这个场景能串起读 issue、选上下文、改代码、跑测试、保持签名校验、阻断 secret 访问、
生成 trace 和 token/cost 报告。它比 calculator demo 更适合展示真实 coding agent runtime。

## 1 分钟版本

我做这个项目的出发点是：LLM 会写代码不等于能安全地自动改代码。真正难的是把模型输出
变成可控动作。Agent Forge 的核心就是一个 CodingAgent runtime：

1. `AgentLoop` 负责 ReAct 闭环：context -> model -> tool call -> observation -> recovery。
2. `ContextStrategy` 控制模型每轮看到什么文件、记忆、规则和工具。
3. `ToolRegistry`、`ToolRouter`、`HookManager` 把工具调用变成可验证、可审批、可恢复的动作。
4. `ExecutionEnvironment`、`CommandPolicy`、`WorkspaceSandbox` 控制路径、命令、网络和工作区。
5. `TraceRecorder` 和 `usage_report` 记录每一步 token、latency、cost、context、tool 结果。

这个项目刻意不做 IDE/TUI，不做云平台，不做模型训练，因为我想把 CodingAgent 最核心的
runtime control plane 做清楚。

## 5 分钟版本

### 1. 背景问题

一个 toy coding agent 通常是：

```text
prompt -> LLM -> tool/function -> final answer
```

这种结构一旦进入真实代码仓库，会遇到几个问题：

- 上下文太大，模型不知道该看哪些文件。
- 工具太多，模型容易选错工具或参数错。
- 工具失败后，如果只是把错误丢回模型，容易循环。
- 写文件、跑命令、访问外部路径都有安全风险。
- 最终说“测试通过”不一定有证据。
- 一次运行花了多少 token、哪里失败、为什么停止，很难复盘。

Agent Forge 的目标就是把这些问题做成 runtime 层的确定性边界。

### 2. 架构主线

主链路是：

```text
run_demo.py
  -> cli.py 组装环境、模型、工具、trace
  -> AgentLoop.run()
  -> ContextBuildReport / ContextStrategy 选上下文
  -> ModelGateway.chat() 调模型并记录 usage
  -> ToolRouter.route() 裁剪工具候选
  -> HookManager.pre_tool() 做权限和环境检查
  -> ToolRegistry.execute() 校验参数并执行工具
  -> Observation 回到下一轮
  -> StepController 决定恢复、停止或继续
  -> TraceRecorder / usage_report 写证据
```

这条链路里，模型只负责提出下一步动作；有副作用、有风险、需要审计的东西都由 runtime 管。

### 3. 两个最强设计点

**上下文工程**

我没有把整个仓库塞给模型，而是拆成：

- `repo_map`：让模型知道项目结构。
- `file_ranker`：按任务词、路径名、内容命中选文件。
- `selected_file_previews`：只给高相关文件的 bounded preview。
- `retrieved_docs`：透明 lexical retrieval。
- `memory_summary`：压缩旧 observation。
- `topic_relation`：判断是否继承上一次 session。
- `FORGE.md`：仓库级规则进入 prompt。
- `budget_breakdown`：记录每段 context 花了多少字符。

这样我能在 trace 里回答“为什么模型看到这些文件、为什么没看到那些文件”。

**执行控制面**

工具调用不是直接执行，而是经过：

- `ToolRouter`：减少工具过载。
- `ToolRegistry`：工具名和参数校验。
- `HookManager`：pre-tool approval、post-tool redaction。
- `PermissionPolicy`：allow / ask / deny。
- `ExecutionEnvironment`：local/worktree、network policy、git 风险命令。
- `CommandPolicy`：只允许 unittest、git status、git diff 这类安全命令。
- `StepController`：失败分类、重复调用检测、max steps、timeout、cost budget。

这使得 agent 的行为不是“靠 prompt 祈祷安全”，而是 runtime 做强约束。

### 4. 主验证场景

我保留 calculator 作为 smoke test，但主场景是 `examples/webhook_service_repo`。

任务是：修复 duplicate `event_id` 导致重复入库和重复 enqueue 的 bug，同时必须保留
signature verification，不读取 `.env` secret，不绕过安全策略，并跑 unittest。

这个场景能验证：

- issue-driven context selection
- 读 handler、tests、docs、security policy
- patch side-effect ordering
- run unittest
- sandbox 阻断 secret 文件
- reviewer gate 拦截 signature bypass
- usage report 记录成本和工具效率

### 5. 成熟度和边界

我不会把它夸大成 Codex 或 Claude Code 同级产品。它没有 IDE/TUI、远程容器集群、真实线上
SLA，也没有模型训练。但它实现了这些产品背后的 runtime core：context、tools、safety、
execution、observability、eval、MCP、review gate。后续如果要产品化，可以在现有边界上接
IDE、Docker/remote sandbox、GitHub PR bot、remote MCP gateway。

## 10 分钟展开顺序

1. 问题定义：LLM 写代码和自动改代码是两回事。
2. 项目定位：production-style CodingAgent runtime core。
3. 主链路：`cli.py -> AgentLoop -> Context -> ModelGateway -> Tool -> Observation -> Trace`。
4. 深讲上下文工程：为什么不全量塞仓库，如何选文件、压缩 memory、处理 topic shift。
5. 深讲工具治理：ToolRouter、ToolRegistry、HookManager、CommandPolicy。
6. 深讲执行控制：StepController、approval mode、worktree、task state。
7. 展示 WebhookPatchBench：任务、风险点、trace、usage report。
8. 展示 eval/review：为什么不信模型自然语言结论。
9. 讲边界：不做 UI/云平台/训练，但保留扩展点。
10. 讲如果继续做：Docker sandbox、provider matrix、自动化 ablation、IDE surface。

## 最重要的三句话

1. “这个项目的核心不是让模型更聪明，而是让模型的动作可控、可审计、可恢复。”
2. “我把 tool calling 当成治理系统，而不是 function call API。”
3. “我用 trace 和 usage report 把每一步 context、token、tool、permission、failure 都量化出来。”
