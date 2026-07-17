# NanoHarness 演进路线

NanoHarness 的目标是成为一个小而硬的 Agent Harness：负责模型与真实开发环境之间的
执行循环、工具治理、持久状态、上下文控制和评测证据。它当前是可运行的 runtime
prototype，不宣称是覆盖所有 Agent 场景的通用框架。

本文件是唯一的项目演进清单。规划项不代表已实现能力；当前事实以
[能力真实性矩阵](CAPABILITY_REALITY_MATRIX.md)和测试为准。

## 当前边界

已经进入真实 Runtime 主链路：

- 单 Agent loop、模型工具调用、动态工具可见性和受限 workspace。
- 写操作审批、HITL 暂停、durable checkpoint、continuation 和副作用幂等账本。
- 有预算的上下文组装、会话压缩和带证据晋升的长期记忆。
- 顺序多角色与隔离 worktree 的 fanout、write-scope 检查、冲突门禁和恢复。
- SWE-bench-shaped 执行、official result 接入、failure taxonomy、matched ablation、
  trace、usage、报告与反馈数据导出。
- 通过 Port/Adapter 替换 Model、Tool、Repository 和 Execution Environment；公共复杂
  入口使用类型化 Request，避免把动态参数表扩散到调用方。

仍然只是 prototype 的部分：

- 默认持久化是本地 JSON，不是支持并发事务的远端状态服务。
- 上下文 token 预算是透明估算，不是每个 provider 的精确 tokenizer。
- fanout 使用静态计划，不是运行中持续重规划的分布式调度器。
- benchmark 代码能产生比较证据，但仓库中的少量运行不能代表稳定模型结论。
- 没有租户、鉴权、在线队列、远端 worker 或生产 SLO。

## 演进原则

1. 先补证据闭环，再增加功能宽度。
2. 新能力必须改变真实 Runtime path，并留下机器可读 artifact。
3. 每个规划项都要有 focused regression 和失败场景，不能只新增 renderer。
4. Candidate patch、local validation、runtime verifier 和 official evaluation 始终分层。
5. 保持当前目录和依赖方向稳定；只有现有边界无法承载需求时才重构。
6. 不为“使用过某框架”引入装饰性依赖。外部框架接入必须有明确互操作需求。

## P0：让评测结论可重复

这是当前最高优先级。项目已经能跑 benchmark，下一步是让结果足以支持工程决策。

| 交付物 | 要回答的问题 | 完成证据 |
|---|---|---|
| 固定 regression manifest | case、commit、环境和配置是否完全一致？ | versioned manifest、dataset digest、environment identity |
| Matched repeated runs | 一次差异是设计效果还是采样波动？ | 每个 variant 至少 3 次重复，报告均值、分布和置信区间 |
| Failure-slice scorecard | 改进解决了哪类失败，又引入了什么回归？ | 按 taxonomy、case 特征和工具阶段切片 |
| Official evaluator lane | Candidate patch 是否真的解决任务？ | per-case official resolved/unresolved/error artifact |
| 成本与延迟基线 | 质量提升是否值得额外 token、tool call 和时间？ | quality/cost/latency 联合比较，原始 run 可追溯 |

验收标准：同一命令可重建实验身份；不匹配的环境会 fail closed；报告明确区分统计事实、
工程解释和未知项。

## P1：冻结可复用 Harness API

目标不是再次重构，而是把现有 runtime 变成别人可以稳定嵌入的最小库边界。

| 交付物 | 设计范围 | 完成证据 |
|---|---|---|
| Stable public facade | `Harness.run(request)`、`resume(request)` 和控制面查询 | 一个外部最小示例只依赖 public API |
| Extension contract tests | Model、Tool、Memory、Policy、Event、State Port | fake adapter 与真实 adapter 运行同一 contract suite |
| Artifact schema versioning | trace、checkpoint、operation、evaluation schema | 旧 fixture migration test 与不兼容变更说明 |
| Lifecycle hooks | before/after model、tool、checkpoint、finalize | hook 顺序、异常隔离和审计测试 |

验收标准：业务接入不需要 import `application` 或 `adapters` 内部模块；新增 provider 或
持久化实现不修改 AgentLoop。

## P2：强化 durable execution

| 交付物 | 核心问题 | 完成证据 |
|---|---|---|
| 精确恢复点 | 进程在 model/tool/checkpoint 任一点中断后从哪里继续？ | kill-at-boundary fault injection，不重复已提交副作用 |
| Cancellation contract | 用户取消后哪些写入已发生，哪些需要补偿？ | side-effect inventory、cancel artifact、补偿结果 |
| Task intent switching | 新意图是补充当前任务、暂停旧任务还是创建新任务？ | 显式 task state machine 与 active-task pointer |
| Deterministic replay | 不调用模型时能否重放决策与证据？ | event schema、input digest、replay divergence report |
| Concurrent state store | 多进程 operator/worker 是否会覆盖状态？ | SQLite/Postgres adapter、乐观锁和并发测试 |

验收标准：恢复语义按 operation 定义，不把“重新运行整段 prompt”当作恢复。

## P3：量化 Context 与 Memory

| 交付物 | 核心问题 | 完成证据 |
|---|---|---|
| Provider-aware token accounting | 预算估算误差会不会触发 overflow？ | tokenizer adapter、估算误差分布、overflow recovery rate |
| Context selection evaluation | 被选文件是否真的包含修复所需证据？ | retrieval recall、noise ratio、missing-evidence slice |
| Compaction quality suite | 摘要是否保留目标、决策、失败和未完成动作？ | 原历史与 digest 的问答一致性、恢复成功率 |
| Memory lifecycle evaluation | 召回是否相关、过期、冲突或污染其他 workspace？ | precision、staleness、conflict、isolation tests |
| Provenance-preserving memory | 一条长期记忆能否回到原始 evidence？ | evidence digest、失效检测、supersede chain |

验收标准：Memory 打开/关闭做 matched ablation；没有证据支持的摘要不能自动成为长期
事实。

## P4：从静态 Fanout 到受控调度

| 交付物 | 核心问题 | 完成证据 |
|---|---|---|
| Plan validator | 哪些任务可以并发，依赖和 write scope 是否完整？ | DAG、读写集和循环依赖检查 |
| Dynamic scheduler | worker 失败、变慢或产生新依赖时如何调度？ | bounded retry、backpressure、worker budget artifact |
| Semantic conflict resolver | 文本可合并是否等于行为不冲突？ | test impact、symbol ownership、集成 verifier evidence |
| Cost-aware routing | 何时 single-agent 比 multi-agent 更合理？ | 相同任务上的质量、延迟、token 和 conflict 对照 |
| Hierarchical handoff | coordinator 给 subagent 的上下文是否最小且充分？ | handoff schema、context attribution、遗漏分析 |

验收标准：并发不是默认卖点；scheduler 必须能给出“不应 fanout”的可解释决定。

## P5：模型与 Harness 共同演进

| 交付物 | 核心问题 | 完成证据 |
|---|---|---|
| Trace-to-dataset pipeline | 哪些运行片段可以转成训练或评测样本？ | 脱敏、去重、provenance 和 schema validation |
| Hard-negative mining | 哪些失败最能暴露模型能力边界？ | taxonomy slice、相似失败聚类、人工审核队列 |
| Harness ablation registry | Prompt、tool policy、memory、planner 的贡献分别是什么？ | 一次只改变一个 factor 的实验记录 |
| Reward signal design | patch、验证、工具效率和安全怎样组成奖励？ | 防 reward hacking case 与离线相关性分析 |
| Behavior compatibility matrix | 不同模型需要哪些 transport 与 tool-call 修复？ | provider/model/version 兼容矩阵和回归集 |

验收标准：训练数据只来自可追溯运行事实；“模型变好”必须由独立评测证明。

## P6：生产执行平面

只有在本地 Runtime 与评测闭环稳定后才进入这一阶段。

- 远端 snapshot/worker API、队列、租约、心跳和 abandoned-run recovery。
- 强隔离执行环境、资源配额、网络策略、secret injection 和 artifact redaction。
- 多租户身份、RBAC、审计日志、retention policy 和删除语义。
- Metrics、distributed trace、error budget、容量规划和成本归因。
- 灰度发布、schema migration、worker 版本兼容和回滚。

验收标准：定义并测量可用性、恢复时间、任务排队时间、执行成功率和单位任务成本；在此
之前不使用“production-ready”描述。

## 明确不做

- 不把 NanoHarness 扩成完整 IDE、聊天产品或模型训练平台。
- 不复制 LangChain 的集成市场，也不复制 LangGraph 的通用图计算 API。
- 不同时维护 Python、Java、Go 多语言实现。
- 不把 MCP server 数量、Agent 数量或 UI 页面数量当作成熟度指标。
- 不在缺少真实需求时增加 LangChain/LangGraph/Spring AI 兼容层。

## 时间有限时的顺序

1. 完成 P0 的 repeated matched benchmark 与 failure slices。
2. 完成 P1 的最小 public facade 和 adapter contract tests，随后冻结架构。
3. 从 P2 只选择 fault injection recovery，从 P3 只选择 compaction quality suite。
4. 用同一 regression set 比较 single、sequential multi 和 fanout，验证 P4 的调度价值。

任何阶段都遵循同一个 Definition of Done：真实路径改变、机器可读 artifact、回归测试、
可复现实验，以及明确说明证据不能证明什么。
