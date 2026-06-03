# 09 Field Readiness Roadmap

这个文件回答一个现实问题：现场追问不只问你代码能不能跑，还会问为什么这样设计、哪里能扩展、怎么评估、怎么控制风险。

项目现在按三层准备：

1. 核心能力进代码：Agent Harness 必须能用 trace 证明。
2. 低频能力进设计口径：能讲清边界和扩展点，不污染 runtime。
3. 手撕题单独准备：不放进项目，避免把 CodingAgent 变成题库。

## 已补进代码的能力

| 追问方向 | 代码证据 | 你可以怎么讲 |
|---|---|---|
| 意图模糊怎么办 | `agent_forge/runtime/clarification.py` | 先做 ClarificationPolicy，不让模型猜；只有不可 grounding 的指代才反问，避免过度澄清。 |
| ReAct / Plan 怎么选 | `agent_forge/runtime/planning_mode.py` | Workflow 适合固定链路，ReAct 适合观察驱动修复，复杂任务先 plan 再 execute。 |
| 上百工具怎么选 | `agent_forge/tools/tool_router.py` | ToolRouter 按 capability/risk/latency/mode 做候选工具裁剪，trace 记录 dropped tools。 |
| Memory 怎么隔离 | `agent_forge/context/memory_policy.py` | MemoryRecord 有 scope、confidence、TTL、source、agent_name，低置信/过期/跨 Agent 私有记忆不会进 prompt。 |
| Prompt 存在哪里 | `agent_forge/runtime/prompt_registry.py` | PromptSpec 有 name/version/purpose/content，ContextBuilder 引用版本化 prompt。 |
| Citation 怎么设计 | `agent_forge/observability/evidence.py` | EvidenceLedger 从 read/patch/command/diff observation 生成可引用证据，final answer 和 usage report 都能看到。 |
| 评测和数据飞轮 | `agent_forge/eval/flywheel.py` | eval 不只看通过率，还按 context/safety/tool/orchestration/coding_benchmark 做能力维度和 badcase 队列。 |

## 已有但要重点讲的能力

| 方向 | 代码证据 | 重点说法 |
|---|---|---|
| Context 工程 | `context_strategy.py`, `repo_map.py`, `file_ranker.py`, `rag.py` | 上下文不是拼 prompt，是策略层：repo map、检索、文件预览、memory、topic shift、token budget。 |
| 工具治理 | `ToolRegistry`, `PermissionPolicy`, `CommandPolicy`, `WorkspaceSandbox` | LLM 只提议动作，runtime 负责 schema、权限、沙箱、命令白名单和失败 recovery。 |
| 多 Agent | `AgentRuntime`, `AgentSpec`, `SupervisorAgent`, `TaskGraph` | 子 Agent 不直接给用户；Supervisor 看 artifact、trace、测试和 review 决定是否 retry。 |
| 执行控制 | `StepController` | max step、timeout、cost budget、重复工具调用、失败分类是 runtime control plane。 |
| 真实运行量化 | `usage_report.py`, `docs/run-artifacts/` | 能讲 token、cache hit/miss、latency、tool efficiency，而不是只说“跑通了”。 |

## 只做边界回答，不进核心代码

| 方向 | 为什么不塞进 runtime | 准备口径 |
|---|---|---|
| 模型训练 / SFT / RL | 当前项目是 Agent Harness，不是训练平台 | 能讲 tool trajectory 数据、observation mask、reward model 适合评估偏好，但不在本项目实现。 |
| 多模态 | CodingAgent 主线是文本代码仓库 | 能讲视觉 token、长耗时任务、异步进度，但作为扩展方向。 |
| GraphRAG / ES / 向量库 | 当前 repo 小，用 lexical + file ranker 足够 | 能讲混合检索、父子 chunk、metadata、版本和有效期；不强行引数据库。 |
| 端云协议 / 能耗 | 本地 harness 没有端侧运行时 | 能讲本地低延迟/隐私、云侧强模型/长任务，协议是未来工程扩展。 |
| DAU / 用户调研 | 项目不是 ToC 产品 | 能讲用户反馈进入 badcase/flywheel，而不是编造业务数据。 |
| 手撕算法 | 和 Agent Harness 无关 | 单独刷 LC 33/792/3/35/1004/200/902，不污染项目。 |

## 现场展开顺序

1. 先讲一句定位：这是轻量 CodingAgent Harness，不是模型训练或 UI 产品。
2. 再讲主链路：CLI -> Context -> ModelGateway -> AgentLoop -> ToolRegistry -> Safety -> Trace/Eval。
3. 然后主动抛两个亮点：Context 工程、执行控制。
4. 如果追问落地经验，就拿 WebhookPatchBench 和 `docs/run-artifacts/webhook-deepseek/usage_report.md` 讲真实 token/cost/latency/tool call。
5. 如果追问缺失能力，用本文件的边界表回答：这个方向知道怎么做，但不塞进当前 runtime，原因是保持项目纯粹。
