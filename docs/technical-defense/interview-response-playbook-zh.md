# Agent Engineer 面试回答手册

这份文档用于准备 Senior AI Agent / CodingAgent 方向面试。回答时不要背概念，要按这个结构讲：

1. 先定义问题解决什么工程风险。
2. 再讲生产系统怎么落地。
3. 然后映射到 Agent Forge 已实现的模块。
4. 最后诚实说明项目没有覆盖的生产化边界。

## 项目一句话

Agent Forge 是一个面向真实代码修复任务的 CodingAgent Harness。它把 SWE-bench issue 转成可复现的 clean repo checkout，再通过 AgentLoop 做上下文工程、工具调用、安全控制、patch 生成，并输出 trace、usage、failure diagnosis 和 benchmark result card。

我不把它包装成完整商业平台，而是聚焦 CodingAgent runtime 最核心的闭环：

```text
SWE-bench issue
  -> checkout base_commit
  -> ContextBuilder selects code evidence
  -> ModelGateway calls DeepSeek/OpenAI-compatible model
  -> ToolRegistry executes read/grep/patch/run/git
  -> Safety layer enforces sandbox/permission/command policy
  -> trace + usage + diagnosis explain success/failure
  -> predictions.jsonl can be sent to official SWE-bench harness
```

## 高频总答法

### AgentLoop 的具体职责

AgentLoop 是 Agent runtime 的控制平面，不只是 while 循环。它负责：

- 接收任务并做 input guardrail。
- 组装当前 step 的上下文。
- 调用模型。
- 解析模型返回的 tool call。
- 做 tool schema、权限、sandbox、command policy 检查。
- 执行工具并把 observation 写回下一轮。
- 记录 trace、usage、checkpoint。
- 判断停止条件，例如 final answer、max steps、timeout、repeated tool call、连续失败。

项目映射：

- `agent_forge/runtime/agent_loop.py`
- `agent_forge/runtime/control.py`
- `agent_forge/observability/trace.py`
- `agent_forge/observability/usage_report.py`

面试表达：

> 我把模型决策和 runtime 控制分开。模型负责提出下一步动作，runtime 决定这个动作能不能执行、怎么审计、失败怎么分类、什么时候停止。这样复杂度不会全部塞进 prompt，系统也更可控、可回放、可定位。

### 为什么复杂 Agent 要把复杂度从 prompt 转移到 runtime

Prompt 适合表达目标和行为偏好，但不适合承担强约束。以下必须由 runtime 实现：

- 路径 sandbox。
- 危险命令拦截。
- 高风险操作审批。
- 最大步数、超时、成本预算。
- 重复工具调用检测。
- 非幂等操作防重。
- trace 和审计。

面试表达：

> Prompt 是软约束，runtime 是硬边界。比如我可以在 prompt 里说不要删文件，但真正防止越权删除必须靠 WorkspaceSandbox、CommandPolicy 和 PermissionPolicy。

项目覆盖：强。

### Chatbot、Workflow、Agent 怎么选

Chatbot 适合信息问答和低风险咨询。Workflow 适合路径确定、规则明确、节点可控的流程，比如退款、审批、客服 SOP。Agent 适合目标明确但路径不确定、需要探索和工具调用的任务，比如修代码、复杂检索、DeepResearch。

面试表达：

> 我不会所有场景都上 Agent。可确定的业务链路优先 Workflow，开放式探索才使用 Agent。生产系统里经常是确定性 workflow 包住局部 ReAct 节点。

项目覆盖：Agent 主线强，Workflow/Chatbot 只在架构上可解释，没有作为产品实现。

## 状态、恢复和停止条件

### 多步任务如何保存中间状态

生产系统里要区分四类状态：

- conversation state：用户对话和意图。
- task state：任务状态机、当前步骤、stop reason。
- artifact state：patch、文件、检索结果、工具输出。
- audit state：trace、usage、permission decision。

项目映射：

- `agent_forge/runtime/task_state.py`
- `.agent_forge/runs/<run-id>/`
- `trace.json`
- `usage.json`

项目缺口：

- 现在能记录 task state 和 trace，但还没有完整实现“从 checkpoint 恢复继续执行”。
- 非幂等工具的 idempotency key 还可以继续补。

### AgentLoop 什么时候停止

不能完全交给模型判断。停止条件由模型和 runtime 共同决定：

- 模型返回 final answer。
- runtime 发现 max steps。
- runtime 发现 repeated tool call。
- runtime 发现 timeout/cost budget。
- permission/guardrail 阻断。
- 连续失败超过阈值。

面试表达：

> 模型可以说“我完成了”，但 runtime 必须判断是否可信、是否越界、是否循环、是否超预算。

## 上下文与记忆

### 框架怎么处理上下文

上下文不是把整个 repo 塞给模型，而是分层选择：

- stable system prefix：固定行为和安全约束。
- task：当前用户目标。
- repo map：项目结构。
- retrieved files：相关文件片段。
- symbol/keyword retrieval：定位函数、类、错误词。
- memory：用户偏好和历史摘要。
- tool schema：当前允许的工具。
- observation history：上一轮工具结果。

项目映射：

- `agent_forge/context/context_builder.py`
- `agent_forge/context/context_strategy.py`
- `agent_forge/context/repo_map.py`
- `agent_forge/context/rag.py`
- `agent_forge/context/symbol_search.py`
- `agent_forge/context/token_budget.py`

### 记忆优先级

优先级建议：

1. system policy
2. developer/business rule
3. 当前用户指令
4. 用户纠正
5. task state
6. 长期记忆
7. 检索文档
8. 模型先验

冲突处理：

- 用户纠正优先于旧记忆。
- 系统/安全规则优先于用户偏好。
- 不确定时反问，不静默合并。
- 长期记忆写入要带来源、时间和置信度。

项目缺口：

- 有基础 memory/context，但还没有完整的长期记忆冲突解决和置信度治理。

## Tool Calling 与 Skill

### Tool 在模型里怎么表示

Tool 是结构化 schema：

- name
- description
- arguments/properties
- required fields

模型输出 tool_call，runtime 校验工具是否存在、参数是否合法、权限是否允许，然后执行工具，生成 observation 回到下一轮上下文。

项目映射：

- `agent_forge/tools/base.py`
- `agent_forge/tools/registry.py`
- `agent_forge/tools/tool_router.py`
- `agent_forge/runtime/llm_client.py`

### Tool 失败怎么办

先分类，再决定是否重试：

- invalid arguments：可修复参数后重试。
- unknown tool：不可重试，提示只能使用已注册工具。
- permission denied：不可绕过，必须停止或请求审批。
- patch mismatch：重新 read file 后修 patch。
- command failed：读错误输出，修代码后跑最小验证。
- repeated action：停止或强制换策略。
- external timeout/5xx：可 backoff retry。
- non-idempotent side effect：不能自动重试。

项目映射：

- `agent_forge/runtime/control.py`
- `agent_forge/safety/permission.py`
- `agent_forge/safety/command_policy.py`

项目缺口：

- 基础 failure classification 已有。
- 外部工具 backoff、idempotency key、补偿事务还可以继续补。

### Skill 版本升级和回滚

生产建议：

- 每个 Skill 有 manifest：name、version、schema、owner、permission、dependency、examples。
- 新版本先跑 regression benchmark。
- 支持 canary 和 shadow。
- 指标变差自动 rollback。
- 旧版本保留一段时间，避免业务突然断。

项目状态：

- 目前有 tool registry、MCP-style adapter，以及本地 `SkillRegistry`。
- `agent_forge/skills/registry.py` 支持 manifest 加载、同名多版本、权限/依赖/owner 元数据、latest resolve 和 rollback target。
- `skill_registry.example.json` 提供了一个可演示的 Skill 版本升级样例。

边界：

- 这不是完整 SaaS 平台的发布系统，还没有线上 canary、流量切分和自动回滚服务。
- 但它已经把面试官最关心的 Skill 治理契约落到代码里：schema、version、permission、dependency、owner、rollback。

## 自演进与评测

### Agent 自演进如何实现

安全的自演进不是让 Agent 在线上随便改自己，而是：

```text
trace/usage/badcase
  -> failure diagnosis
  -> 生成候选 prompt/retrieval/tool policy 改动
  -> fixed regression set
  -> 指标对比
  -> canary
  -> rollback
```

项目新增覆盖：

- `--showcase`：固定一个复杂样例。
- `--regression-set core`：固定多 case 回归集。
- `failure_class / diagnosis / next_actions`：失败归因。
- UI evidence dashboard：展示结果、usage、timeline。

项目仍缺：

- 自动生成候选 prompt/policy。
- 在线 canary。
- regression trend 历史对比图。
- 自动 rollback。

### 为什么评测不能只看最终成功率

Agent 是过程型系统，只看 success rate 不够。还要看：

- patch 是否生成。
- official eval 是否通过。
- token/cost。
- latency。
- tool failure rate。
- repeated tool call。
- context 是否命中关键文件。
- guardrail 是否触发。
- failure diagnosis。

项目映射：

- `agent_forge/bench/swebench.py`
- `agent_forge/bench/diagnostics.py`
- `agent_forge/ui.py`

## Multi-Agent

### 为什么要多 Agent

多 Agent 适用于：

- 角色天然不同：planner/coder/reviewer/tester/security。
- 需要互相校验。
- 子任务可并行。
- 权限隔离明显。
- 上下文隔离明显。

单 Agent + Skills 适用于：

- 路径不太复杂。
- 工具调用可以串行。
- 不需要角色互审。
- 没有明显并行收益。

项目状态：

- 当前主线是 single CodingAgent。
- 我没有把 multi-agent 放进主线，因为 SWE-bench 修复首先要证明单 Agent harness 的上下文、工具、安全、评测闭环。

面试表达：

> 我不会为了概念上高级就上 multi-agent。生产上 multi-agent 要解决通信成本、状态所有权、停止条件和输出验证，否则容易变成多个模型互相聊天。

## 性能与成本优化

### 性能问题在哪

Agent 的瓶颈通常在：

- LLM latency。
- prompt token 太长。
- 工具调用串行。
- 外部系统慢。
- 检索召回太多或不准。
- 失败重试和重复工具调用。

项目怎么定位：

- Usage Dashboard 看每 step token/cost。
- Trace Timeline 看工具调用顺序和失败点。
- Context Breakdown 看上下文预算花在哪。
- Tool Efficiency 看工具成功率和耗时。

### system prompt 前缀稳定

前缀稳定是为了 prompt cache：

- system prompt 固定。
- tool schema 顺序稳定。
- 安全规则稳定。
- 动态任务和 retrieved context 放后面。

这样模型 provider 更容易命中 cache，降低成本和延迟。

项目覆盖：

- context strategy 中已经有 stable prefix 意识。
- usage report 记录 cache hit/miss。

## 安全与风控

### System prompt 和 runtime guardrail 边界

System prompt 是软约束，runtime 是硬约束。以下必须靠代码：

- 文件路径 sandbox。
- 危险命令阻断。
- 敏感文件保护。
- 写操作审批。
- 网络/外部命令策略。
- max steps、timeout、cost budget。
- trace 和 audit。

项目映射：

- `agent_forge/safety/sandbox.py`
- `agent_forge/safety/command_policy.py`
- `agent_forge/safety/permission.py`
- `agent_forge/safety/guardrails.py`

## C 端 AI 搜索/客服类系统设计

### AI 搜索/对话端到端链路

```text
query
  -> intent classification
  -> query rewrite
  -> retrieval/search/web/tool
  -> rerank
  -> answer generation
  -> citation/factuality check
  -> safety
  -> streaming response
  -> feedback/badcase loop
```

项目覆盖：

- 当前项目不是 C 端搜索产品。
- 可借鉴的是 trace、tool governance、evaluation loop。

### 智能客服和 FAQ 区别

FAQ 是检索答案；客服 Agent 是带状态和工具的业务执行系统：

- 查订单。
- 判断规则。
- 申请退款/补偿。
- 必要时转人工。
- 保留审计。

项目覆盖：不直接覆盖，可以作为系统设计题讲。

## 平台化与生产化

平台层应该提供：

- model gateway
- tool registry
- skill registry
- workflow engine
- permission/RBAC
- quota/rate limit
- trace/audit
- eval/regression
- release/canary/rollback

业务层负责：

- prompt
- workflow
- domain tools
- business policy
- badcase set

项目覆盖：

- model gateway、tool registry、safety、trace、eval 有基础。
- 多租户、quota、release/canary、Skill Registry 还没有完整生产实现。

## 开源项目质量保障

回答框架：

- CI smoke test。
- regression benchmark。
- release candidate。
- changelog。
- semantic versioning。
- backward compatibility policy。
- 外部 PR 需要最小复现、测试、review。
- SDK 行为变化用 bisect + regression test 定位。

项目当前：

- 有 smoke test。
- 有 SWE-bench-style benchmark loop。
- 新增固定 regression set。
- 还缺正式 release/versioning 策略。

## 行为题怎么把项目讲出来

可以这样讲：

> 我主导做了 Agent Forge，从一开始的 toy demo 收敛到真实 SWE-bench 风格闭环。我的重点不是堆很多概念，而是把 CodingAgent 的关键工程面做清楚：AgentLoop 控制、多层上下文、工具治理、安全边界、trace/usage、failure diagnosis 和固定 regression set。这样每次失败不是一句“模型不行”，而是能定位到 repeated tool、context miss、tool failure、budget exhausted 或 official eval failure。

## 手撕代码准备重点

算法：

- 反转链表。
- 合并两个有序链表。
- 最大子数组和。
- 无重复字符最长子串。
- LRU Cache。
- 最小覆盖子串。

工程：

- Tool Router。
- JSON schema 校验和 repair prompt。
- 目录 diff。
- Shell top 10 files。
- 进程存活检测和拉起。
- 简单 DB Skill。

JSON 稳定返回回答：

> 优先使用 provider 原生 structured output 或 tool call。runtime 用 JSON schema 校验；不合法时把 parse error 和 schema 发回模型做 repair，设置最大重试次数。仍失败就返回结构化错误，而不是让下游吃半结构化文本。

项目映射：

- `agent_forge/runtime/structured_output.py` 负责抽取 JSON、校验 schema、生成 repair prompt。

## 项目缺口与是否值得补

| 能力 | 当前状态 | 面试价值 | 建议 |
| --- | --- | --- | --- |
| SWE-bench showcase | 已有 | 高 | 继续保留 |
| Fixed regression set | 已补 | 高 | 后续增加趋势对比 |
| Failure diagnosis | 已补 | 高 | 后续沉淀更多规则 |
| Skill Registry/versioning | 基础实现已补 | 高 | 后续接 canary/发布系统 |
| Resume/idempotency | 部分 task_state | 高 | 值得补 |
| JSON repair prompt | 基础实现已补 | 中高 | 后续接 provider 原生 structured output |
| Multi-agent distributed | 未实现 | 中 | 文档解释即可 |
| C 端搜索/客服完整系统 | 未实现 | 中 | 作为系统设计题准备 |
| 多租户 SaaS 平台 | 未实现 | 中 | 不建议硬塞代码 |
| 离线 RL/自演进训练 | 未实现 | 中 | 文档讲架构，不硬做 |
