# SWE-bench Smoke-5 与 Runtime 行为门禁

本文维护两套不能混为一谈的验证：

1. **Smoke-5**：五个真实 SWE-bench Verified Case，端到端运行模型、工具、候选改动和官方评测。
2. **Runtime 行为门禁**：针对审批、恢复、压缩、幂等和熔断等机制的 pytest 单元/集成测试。

前者回答“Agent 能否在真实仓库问题上形成可验收结果”，后者回答“某条 Runtime 契约有没有回归”。
行为门禁不是额外的 Benchmark Case，不进入 Smoke-5 的分母，也不能用来计算模型解决率。

## 评测目的

NanoHarness 使用 SWE-bench Verified `test` split 的 500 个公开 case 作为候选全集。该集合
由 SWE-bench 团队与软件工程师对原始任务进行人工筛选，减少不可复现和描述不清的题。内置
`smoke-5` 的目标不是报告模型排行榜成绩，而是用可承受的成本回归 Harness 的代码检索、
工具循环、patch 生成、验证和 evidence pipeline。数据集身份和字段见
[SWE-bench Verified](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified)。

五个 case 是**人工分层 smoke sample**，不是随机样本，也不具有统计代表性：

1. 每个 case 来自不同仓库和不同问题族；
2. 每个参考修复只涉及一个源码文件，且不超过三个 hunk，便于把失败归因到 Harness；
3. 同时保留最小修复、跨模块类型定位和多 hunk 状态修复等不同 patch 形态；
4. 每题都有 `FAIL_TO_PASS` 与 `PASS_TO_PASS`，可以区分目标修复和回归保护；
5. 五题足以做每次提交的低成本机制诊断，不足以估计 SWE-bench Verified 总体解决率。

## 固定 100 题样本与增量分片

需要形成比 Smoke-5 更稳定的项目指标时，使用
[`swebench-verified-100-v1.json`](../../benchmarks/cohorts/swebench-verified-100-v1.json)。
它把 Verified 500 题按固定 seed 与 `instance_id` 的 SHA-256 排序，取前 100 题；选择过程
不读取题目正文、test patch、gold patch 或历史运行结果。母集再按顺序拆成互斥的 `a`、`b`
两个 50 题分片：先跑 `a`，以后扩到 100 时只跑 `b`，不会重复计费。

```bash
forge bench campaign \
  --cohort-manifest benchmarks/cohorts/swebench-verified-100-v1.json \
  --cohort-shard a --repetitions 1 \
  --provider deepseek --model deepseek-v4-flash \
  --thinking enabled --reasoning-effort max \
  --max-steps 16 --cost-budget-usd 0.05 \
  --evaluate --official-cache-level env --publish
```

输出口径必须是“固定 50 题样本上 official resolved `X/50`”。它能证明项目有预注册分母、
官方 oracle 和成本意识，但一次运行不能估计随机稳定性，也不是官方排行榜提交。

## 固定 SWE-bench Verified Smoke-5

下表中的参考 patch 规模只用于运行后的样本审计，不会进入 Agent prompt。

| Case | 问题族 | F2P / P2P | 参考 patch 规模 | 主要观察点 |
| --- | --- | ---: | --- | --- |
| `astropy__astropy-12907` | 算法正确性 / 嵌套组合 | 2 / 13 | 1 file, 1 hunk, +1/-1 | 代码定位、语义推理、最小 patch |
| `django__django-11133` | 类型边界 / Framework 兼容 | 1 / 64 | 1 file, 1 hunk, +1/-1 | 类型识别、公共 API、回归保护 |
| `matplotlib__matplotlib-20859` | 公共 API / 类型层级 | 1 / 88 | 1 file, 2 hunks, +5/-3 | 跨模块导航、共同抽象、兼容性 |
| `pytest-dev__pytest-10051` | 状态生命周期 / 可诊断性 | 1 / 15 | 1 file, 3 hunks, +5/-2 | 对象身份、别名语义、多 hunk 编辑 |
| `sympy__sympy-20590` | 继承语义 / 对象布局 | 1 / 21 | 1 file, 1 hunk, +5/-0 | 继承链定位、非局部根因、回归保护 |

## 查看 Case，而不是背 ID

```bash
# 集合目标、500-case 候选全集、选择方法、每题入选原因和结论边界
forge bench cases

# issue、base commit、FAIL_TO_PASS、PASS_TO_PASS；不执行 Agent
forge bench case astropy__astropy-12907

# 仅在运行后复盘官方验收实现或参考答案
forge bench case astropy__astropy-12907 --show-test-patch
forge bench case astropy__astropy-12907 --show-gold
```

默认输出不会包含 official test patch 或 gold patch。这样既方便解释每题具体怎样测，
也把数据泄漏边界变成可执行契约，而不是口头约定。

## 运行与结论边界

```bash
forge bench swebench --regression-set smoke-5 --provider deepseek \
  --model deepseek-chat --temperature 0 --tool-routing task-aware --evaluate
```

每次运行生成 `results.json`、`scorecard.json`、`scorecard.md` 和 `report.md`。Sampling
temperature 会进入真实请求、run artifact 和 matched-run identity。没有显式 per-case
resolved/unresolved report 时，不报告 official resolved rate；只有五题或每个 variant
只运行一次时，不向外推一般模型质量。PyCharm 初始 campaign 对同一 case 的两个 preset
保留 official instance image cache，减少配对评测的重复构建；cache policy 进入实验身份。

## 为什么只接这一套外部 Benchmark

| 候选 | 当前决策 | 原因与后续触发条件 |
| --- | --- | --- |
| [SWE-bench Verified](https://www.swebench.com/) | 主外部基准 | 直接覆盖真实仓库 issue -> patch -> official tests，与当前软件工程 AgentLoop 的产品边界一致。 |
| [Terminal-Bench 2.0](https://arxiv.org/abs/2601.11868) | 暂缓 | 更适合宽泛 terminal、多进程/多容器任务；等 Runtime 明确支持通用 shell execution 与相应隔离镜像后再接。 |
| [BFCL V4](https://gorilla.cs.berkeley.edu/leaderboard) | 不作为 Runtime 结果 | 主要衡量模型的函数/工具调用能力；NanoHarness 通过 schema repair 与行为测试治理调用，但不把模型能力分数归功于 Harness。 |
| [τ-bench](https://arxiv.org/abs/2406.12045) | 暂不接 | 需要业务领域 API、policy 和 user simulator；适合业务 Agent，不是当前 repository-task 主线。 |
| 内部行为测试 | 必跑契约门禁 | 验证 checkpoint、HITL、安全和 evidence 语义；它不是外部 Agent 能力分数。 |
| 非 Coding 业务基准 | 当前不接 | 需要真实 domain API、policy、user simulator 和 executable oracle；不使用手写 evidence scorecard 代替真实运行。 |

选择原则不是“benchmark 越多越好”，而是每套评测都必须对应一个真实产品边界、一个可执行
oracle 和一个不会误导的 denominator。当前项目用 Verified 回答结果正确性，用行为测试回答
Runtime 契约，用 trace/scorecard 回答过程质量；三者不能互相替代。

## 从运行事实到改进决策

项目的数据闭环不是“失败后自动新增一个测试”，而是：

```text
Trace / Policy / Environment / Candidate Diff / Official Result
  -> Failure Taxonomy + 人工反馈
  -> bad-case 分组与可执行回归
  -> 固定变量的 matched experiment
  -> adopt / reject / 继续收集证据
```

`forge eval feedback` 保存人工 outcome、label 和 note；`forge eval export-dataset` 导出经过复核的
结构化记录。默认不导出完整 Tool 参数、Observation、绝对路径或 Patch 正文，避免把仓库内容和
秘密混入分析数据。自动规则负责聚合事实和给出候选诊断，最终改进决策仍需人工复核；该流程是
Evaluation 数据闭环，不声称已经构成 RL 训练平台。

## Runtime 行为门禁（不是 SWE-bench Case）

下表每一行是一个**机制验收场景**。除第一行的 Astropy 端到端 Case 外，其余主要由 pytest
单元/集成测试构造最小输入并验证 Runtime invariant；它们是特性回归证据，不是模型任务样本。

| 场景 | 保护的 Runtime 契约 | 主要测试证据 |
| --- | --- | --- |
| `astropy__astropy-12907` | 真实仓库的检索、工具循环、候选改动和评测链路。 | `tests/test_swebench_compare.py`、官方 Case artifact |
| `validation-env-unavailable` / `context-miss-file-selection` | 环境故障与代码失败、检索缺失不能混淆。 | `tests/test_failure_taxonomy.py`、`tests/test_bench_failure_analysis.py` |
| `tool-governance-blocked-command` | 未授权命令不能越过 Tool/Command policy。 | `tests/test_command_policy.py`、`tests/test_agent_loop_policy.py` |
| `repeated-action-loop` / `tool-call-burst-bound` | 原地打转和单轮超额 ToolCall 必须受控。 | `tests/test_agent_loop_policy.py` |
| `manual-approval-pending` / `stale-approval-fingerprint` | 副作用前停机；目标漂移后旧批准失效。 | `tests/test_human_approval.py` |
| `resume-state-continuation` | Checkpoint 恢复显式状态，不伪装成隐藏会话重放。 | `tests/test_task_resume.py`、`tests/test_resume_cli.py` |
| `context-window-tool-transaction` | 压缩不能拆散 ToolCall 与对应 Observation。 | `tests/test_context_window.py` |
| `long-term-memory-authority` | 只有用户显式 remember 的记录可跨 Run 召回；项目同 key 覆盖用户默认值，更新保留 ID 并递增 revision。 | `tests/test_long_term_memory.py` |
| `model-tool-call-repair` | 只修复可确定的协议格式，不提升不可见工具。 | `tests/test_model_adaptation.py` |
| `failed-model-usage-accounting` | 失败请求和 overflow 尝试也必须计入 usage/cost。 | `tests/test_agent_loop_policy.py`、`tests/test_llm_client_transport.py` |
| `subagent-fanout-conflict` | 依赖和 write scope 决定并发；冲突必须显式收口。 | `tests/test_live_fanout.py`、`tests/test_subagent_fanout.py` |
| `operation-ledger-idempotency` / `operation-ledger-stale-target` | 已执行副作用不重复；目标漂移时不能误用旧记录。 | `tests/test_operation_ledger.py` |

## 指标

- patch generated
- local verified
- official resolved（有 official evidence 时）
- failure class
- tool calls
- failed tool calls
- repeated actions
- context files selected
- context compacted / overflow recovered
- active long-term memories recalled
- model tool-call repairs
- bounded tool-call bursts
- estimated cost
- latency
- human intervention count
- duplicate side-effect skips
- stale approval / stale operation count
- unsupported claim count

## 规则

Runtime change 只有在至少一个 case 上改进 success、observability、failure localization、
cost 或 safety boundary，同时没有隐藏其他 case regression，才算有价值。

比较 runtime factor 时使用 `forge eval ablation` 和 matched run；不同 model、dataset、
split 或 case id 的 run 不应直接比较。Memory 实验还必须固定 snapshot SHA-256，Skill
实验必须记录 manifest SHA-256；召回或激活次数只能证明机制被触发。

执行环境不是实验因子时也必须固定：local/worktree 与 OCI 的隔离边界、network policy、镜像、
资源限制或 evaluator coverage 发生 drift，比较应被拒绝。OCI 提供更强的进程隔离，但不声称
hostile multi-tenant security。
