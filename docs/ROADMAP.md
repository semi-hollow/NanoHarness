# NanoHarness 演进路线

NanoHarness 保持“小而硬”：先用真实 repository task 和 evaluation evidence 验收现有 Runtime，
再扩大能力。规划项不代表已实现；当前事实以
[能力真实性矩阵](CAPABILITY_REALITY_MATRIX.md)和测试为准。

## 决策原则

1. 先补结果证据，再增加功能宽度。
2. 新能力必须进入真实 Runtime path，并产生机器可读 artifact。
3. 每项改动必须有失败场景、focused regression 和 claim boundary。
4. Candidate、local validation、runtime verifier、official evaluation 始终分层。
5. 当前 Capability 与依赖方向已经冻结；真实边界无法承载需求时才重构。
6. 不为框架关键词增加装饰性 adapter、preset 或 demo。

## Now：完成可复现效果证据

这是唯一 P0。Campaign 执行器、恢复、聚合和公开 bundle 已完成，真实 official repeated
result 仍待运行。

| 交付物 | 要回答的问题 | 完成证据 |
| --- | --- | --- |
| 10-run pilot | Evaluator、成本和 artifact contract 是否可用 | 五题、两个 preset、一次重复的完整 official outcome |
| 30-run campaign | 差异能否跨 repetition 保持 | 五题、两个 preset、三次交错重复与 per-slot checkpoint |
| Failure slices | 改进解决了什么，又引入什么 | taxonomy/case/tool-stage 切片与代表性 case study |
| Cost-quality view | 正确性变化是否值得成本 | paired outcome、token、latency、tool failure 联合比较 |
| Public bundle | 外部读者能否复核每个数字 | source/config digest、脱敏 scorecard 与可校验 SHA |

验收：没有 official denominator 就不发布 correctness claim；multi-factor preset comparison
不伪装成单因素因果实验。

## Next：只补三个高价值缺口

### 1. 持久执行故障注入

在 model、tool、checkpoint 边界主动 kill process，验证 continuation 不重复已提交副作用；
补远端命令取消语义、side-effect inventory 和并发 state-store 乐观锁。

完成证据：kill-at-boundary matrix、duplicate/stale regression、replay divergence report。

### 2. Context 与 Memory 质量评测

测量 token 估算误差、retrieval recall/noise、digest 保真度、memory precision/staleness 和
workspace isolation，而不是只记录 recall 次数。

完成证据：Memory/compaction matched ablation、带 provenance 的错误样本和恢复成功率。

### 3. Adapter contract 与 artifact schema

冻结 `Harness` public facade；让 Model、Tool、State、Event、Environment adapter 运行同一套
contract test，并为 checkpoint/trace/evaluation schema 增加版本与 migration fixture。

完成证据：外部 consumer 不 import internal module；新增 provider 不修改 AgentLoop。

## Later：有真实需求后再做

| 方向 | 触发条件 | 最小验收 |
| --- | --- | --- |
| 会话级 Task switch | 用户确实需要多任务暂停与切换 | active-task state machine、优先级和恢复测试 |
| 动态 Fanout scheduler | 静态 DAG 在真实任务中成为瓶颈 | backpressure、bounded retry、cost-aware single/fanout decision |
| 语义冲突解决 | 文本可合并但行为冲突成为主要失败 | symbol/test ownership 与 verifier evidence |
| Trace-to-dataset | 存在真实训练或数据协作方 | privacy、dedupe、provenance、version manifest 与人工审核 |
| 远端执行平面 | 本地 worker 无法满足团队规模 | queue、lease、heartbeat、RBAC、SLO、成本与隔离验证 |
| LangGraph 互操作 | 团队接入需要而非简历关键词 | 一个 node/middleware 示例复用现有 policy 与 evaluation |

Later 不是承诺排期，只证明系统边界和演进方向已经考虑。

## 明确不做

- 不扩成完整 IDE、聊天产品、通用 RAG 应用或模型训练平台。
- 不复制 LangChain 集成市场或 LangGraph 通用图 API。
- 不同时维护 Python、Java、Go 多语言实现。
- 不把 MCP server、Agent、UI 页面或文档数量当作成熟度指标。
- 不在缺少 official evidence 时继续堆新的 Runtime 名词。

## 完成定义

任何 Roadmap 项只有同时满足以下条件才算完成：

```text
真实路径发生改变
+ 机器可读 artifact
+ regression / fault case
+ 可复现实验
+ 明确不能证明什么
```

时间有限时只做：official campaign -> fault injection -> context/memory quality。其他内容不阻塞
项目投递和现阶段架构冻结。
