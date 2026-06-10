# 05 Technical Defense Brief

这份文件用于快速组织讲法，不重复代码细节。

## 开场定位

Agent Forge 是 production-style CodingAgent runtime core。它关注的不是 UI
外壳或模型训练，而是让 LLM 在受控代码执行系统里工作：上下文工程、模型网关、工具治理、
执行环境、审批 hooks、任务状态、审查门禁、trace、usage 和 eval regression。

## 两个最强设计点

**1. Context Engineering**

不是把仓库全塞给模型，而是 repo map、file ranker、lexical retrieval、selected file preview、
memory summary、topic relation、FORGE.md 和 budget breakdown 的组合。

**2. Runtime Control Plane**

模型只提出工具调用。系统用 ToolRouter、HookManager、ExecutionEnvironment、PermissionPolicy、
CommandPolicy、ToolRegistry、StepController、TaskStateStore 把动作变成可控、可恢复、可审计的执行。

## 高频追问速答

| question | answer |
|---|---|
| 为什么用 ReAct？ | Coding task 需要观察驱动：读文件、patch、看测试输出、失败恢复。 |
| Workflow 什么时候更好？ | 固定链路、高风险业务流程、强可控节点适合 workflow。局部节点再用 ReAct。 |
| Tools / Workflow / Agent 区别？ | Tool 是动作接口，Workflow 是确定性编排，Agent 是模型参与决策的闭环 runtime。 |
| 上百工具怎么选？ | ToolRouter 先按 capability/risk/latency/mode 和任务词裁剪；外部工具也走同一 schema。 |
| tool call 失败怎么办？ | ToolRegistry 返回 Observation，StepController 分类 unknown/invalid/permission/patch/command/repeated。 |
| 怎么避免越权？ | ExecutionEnvironment + WorkspaceSandbox + CommandPolicy + ApprovalMode + HookManager。 |
| 怎么做 human-in-loop？ | hook 返回 ASK，AgentLoop 写 human_approval event；非交互环境可 locked/dry-run。 |
| 怎么支持长任务？ | TaskState checkpoint + trace replay + resume seed。 |
| 子 agent 幻觉怎么办？ | Supervisor 不信文本，信 artifact、trace、test observation、review gate。 |
| 怎么讲成本？ | usage_report 有 per-step token、cache hit/miss、cost、latency、tool efficiency。 |
| 为什么不做训练？ | 本项目是 runtime core；训练属于模型能力层，和执行控制面分层。 |
| 为什么不用某框架？ | 自研 runtime core 是为了把关键边界讲清；生产可迁移到 LangGraph/CrewAI/OpenAI Agents。 |

## 不在本仓库里的产品外壳

这些不是忽略，而是分层边界：

- IDE/TUI：产品交互层。
- 云容器平台：部署和资源隔离层。
- 多模态生成：任务类型扩展层。
- SFT/RL：模型训练层。
- 大规模 RAG/GraphRAG：知识平台层。

回答方式：本仓库实现 runtime core；这些能力可以接在 runtime 边界外，不应该全部塞进一个代码仓库。

## 现场演示路径

```bash
# 主验证场景
local_scripts/run_webhook_deepseek.sh

# 看量化结果
open .agent_forge/latest/webhook-deepseek/usage_report.md

# 看审查门禁
python run_demo.py --mode review

# 看任务状态
python run_demo.py --list-task-states
```

讲的时候按这个顺序：

1. 先讲 runtime core 的定位。
2. 再讲 `single` 主链路。
3. 主动展开 context engineering 和 runtime control plane。
4. 用 WebhookPatchBench 的 usage report 讲真实 token/cost/tool/trace。
5. 最后讲 review/eval/task-state 如何支持上线后的迭代。
