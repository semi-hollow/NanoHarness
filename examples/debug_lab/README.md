# NanoHarness Debug Lab：只掌握三条主线

这里是项目唯一的动态学习入口。目标不是读完全部代码，而是让你对三个面试核心场景拥有
**定位、预测、观察、故障注入、证据回放和设计解释**六种控制力。

三个 Lab 都进入正式 Runtime；`examples/debug_lab` 只固定输入、隔离 workspace 并发布 Evidence。
确定性模型不是伪造 Runtime，它只移除在线模型随机性，让你先看清控制逻辑。

本文件是 Debug、Workbench 和面试演练的唯一说明。不要再从多份清单拼接学习顺序。

## 首次准备

关闭 PyCharm，在项目根目录只执行一次：

```bash
scripts/setup_macos_local.sh --quick
```

然后用 PyCharm 单独打开 NanoHarness 根目录。脚本会安装按源码 symbol 定位的断点；项目已共享
四个 Run Configuration。若安装时 PyCharm 已打开，关闭后再执行：

```bash
.venv/bin/python scripts/install_pycharm_debug_lab.py
```

安装器只保留 14 个主链断点，并按 Run Configuration 自动隔离：Lab 1 触发 7 个、Lab 2 触发
5 个、Lab 3 触发 2 个。运行一个 Lab 时不会停进另外两个 Lab 的代码。

学习时只使用 PyCharm 的绿色 Debug 按钮；不要记长命令，也不要进入第三方库。Project 面板先选择
`00 NanoHarness Review Path`，需要看 Lab 2/3 的 owner 时切到 `05 NanoHarness Extended Flows`；
查调用时把 Find Usages Scope 限制在这两个范围，只有验证行为时才切到 `90 NanoHarness Tests`。

## 只保留四个按钮

| 顺序 | PyCharm 配置 | 作用 | 模型与成本 | 怎么用 |
| --- | --- | --- | --- | --- |
| 1 | `NanoHarness Lab 1 - Governed Repair` | 写请求 → 审批暂停 → checkpoint → continuation → 写入 → pytest | 确定性 ModelPort；无 API 费用 | **必学**，Debug 看状态，结束后自动打开 Workbench |
| 2 | `NanoHarness Lab 2 - Coordinated Agents` | DAG → 隔离 worker 并发 → scope 校验 → diff 合并 → finalizer | 确定性 ModelPort；无 API 费用 | **必学**，Debug 看并发与合并，结束后自动打开 Workbench |
| 3 | `NanoHarness Lab 3 - Evaluation Loop` | campaign → 分层 correctness → 诊断 → 前后对比 → 人工决策 | 回放已保存 Evidence；不调用模型或 Docker | **必学**，Debug 看评测闭环，结束后自动打开 Workbench |
| 可选 | `NanoHarness Operator Console` | 真实模型、交互输入、审批与 continuation | 需要 DeepSeek API Key，会产生费用 | 只体验产品效果，不进入首次代码主线 |

每个 Lab 只跑到下方“学会标准”全部通过。通过后停止，不继续扩展模块。

PyCharm 额外显示的 `Current File` 是 IDE 临时动作，不属于 NanoHarness。项目约束是共享配置始终
只有以上四个；新能力必须进入现有主线，不能继续增加按钮。

## Lab 1：受治理修复

主要入口：

```text
examples.debug_lab.run.run_governed
→ Harness.run
→ AgentLoop.run
→ ToolExecutionPipeline._execute_call
→ OperationTracker
→ ToolAuthorizationGate
→ RunLifecycle.finalize_run
```

第一次 Debug 只观察五个对象：

1. `session`：一次 run 的消息、观察、工具历史和生命周期。
2. `tool_call`：模型提出的意图，不代表工具已被允许执行。
3. `operation_intent.key/fingerprint`：同一副作用的身份与目标前置状态。
4. `checkpoint`：暂停后可持久化恢复的业务状态，不是 Python 调用栈。
5. `validation_evidence`：continuation 后真实 focused pytest 的结果。

故障注入不再单独建 Lab。在 PyCharm 中运行
`tests/test_operation_ledger.py::OperationLedgerTest::test_resume_blocks_when_crash_leaves_operation_outcome_unknown`，
观察“文件已写入、ledger 仍是 executing、恢复后拒绝重复执行”。正确口径是
**fail closed、偏向 at-most-once，不是 exactly-once**。

学会标准：

- 30 秒内找到 `Harness.run` 和 `ToolExecutionPipeline._execute_call`。
- 在审批前预测状态为 `waiting_approval`，并指出哪个 artifact 保存决定。
- 解释为什么 approve 绑定 operation key，而不是绑定自然语言。
- 解释 crash window 为什么需要 `operation_outcome_unknown`，以及为什么不能自动重放。
- 在 Workbench 的 `1 Governed Run` 和 `Timeline` 找到审批、checkpoint、执行与 pytest 证据。

## Lab 2：协作式多 Agent

主要入口：

```text
examples.debug_lab.run.run_coordinated
→ LiveFanoutCoordinator.run
→ build_conflict_free_batches
→ LocalAgentWorkerAdapter.run_worker
→ LiveFanoutCoordinator._merge_batch
→ LocalAgentWorkerAdapter.run_finalizer
```

固定任务把 `pricing.py` 和 `shipping.py` 分给两个 worker。它们在独立 Git worktree 中运行真实
AgentLoop，写集合互不相交，因此进入同一并发批次。Coordinator 校验实际 touched files，再按稳定
顺序合并 candidate diff；只有全部 worker 成功且无冲突，finalizer 才执行 `test_checkout.py`。

第一次只观察：

1. `plan.tasks` 的 `depends_on`、`write_scope` 和 `max_steps`。
2. `dependency_batches` 为什么把两个任务放入同一批。
3. 两个 worker 的 `active_workspace` 为什么不同。
4. `completed_batch_results` 的 touched files、diff 和状态。
5. `merged_task_ids`、`integrated_diff_path` 与 finalizer pytest。

学会标准：

- 能画出 `DAG → batch → worker worktree → scope gate → merge → finalizer`。
- 能解释“声明 scope”不够，为什么还要检查实际 touched files。
- 能回答有依赖任务为何串行、无写冲突任务为何可以并发。
- 能说明这不是分布式 worker service，也不是模型自动拆任务。
- 能在 Workbench 的 `2 Coordinated Agents` 直接看到 worker 结果、修改内容、合并和 finalizer。

## Lab 3：评测改进闭环

主要入口：

```text
examples.debug_lab.run.run_evaluation
→ published campaign manifest/summary
→ failure taxonomy provenance
→ improvement_record.json
→ Workbench Evaluation & Improvement
```

该 Lab 不重新付费调用模型，也不重跑 Docker。它回放仓库中真实保存的
`astropy__astropy-12907` 与 `django__django-11133` 两个 SWE-bench commissioning case，
并生成一条可审计改进记录：

```text
问题 → 诊断来源/人工复核 → 假设 → Runtime preset 改动
→ matched regression cases → before/after → adopt/iterate/reject
```

当前真实证据是：两种 preset 都 official resolved 2/2；governed-runtime 的失败工具调用
`8 → 5`，但 token 与成本上升。因此决策是 `iterate`，不能声称 correctness 提升或总体成功率。

学会标准：

- 能区分 candidate patch、local validation、official evaluation 三层证据。
- 能说明 taxonomy 是有版本和命中规则的自动诊断，人工复核是另一层事实。
- 能从 `summary.json` 找出 before/after 指标，从 `improvement_record.json` 找出最终决策。
- 能解释为什么两个 post-hoc case 只算 commissioning evidence。
- 能在 Workbench 的 `3 Evaluation Loop` 讲完问题、假设、指标、trade-off 和 claim boundary。

## 统一验收：六问不过，就不算学会

每条主线都必须脱离文档回答：

| 验收动作 | 通过标准 |
| --- | --- |
| 定位 | 30 秒内找到外围入口和一个核心 owner |
| 预测 | 断点执行前说出下一状态和关键数据变化 |
| 观察 | 在 Variables/Watch 中指出实际输入、输出和状态 |
| 故障 | 能运行或口述一个真实 failure injection 及保护行为 |
| 证据 | 在 Workbench 找到对应 artifact，而不是打开原始 JSON 硬讲 |
| 解释 | 说清动机、备选方案、取舍和不能声称什么 |

三条全部通过，就已经满足开始面试的项目掌握门槛。**不要等待读完仓库，也不要把 NanoHarness
当作日常 Claude Code 替代品。**它是可重复实验台：面试前每周各跑一次、刻意注入一次失败、
脱稿讲一次，收益高于低效率地拿它完成日常编码。

## Workbench 怎么看

三个 PyCharm Lab 跑完后会自动启动或复用 Workbench，并打开本次 Lab 对应的场景。
如果只想重新查看已有 Evidence，可以双击 `scripts/start_workbench.command`。首页只保留三个学习场景：

- `1 Governed Run`：权限、审批、checkpoint、恢复和副作用治理。
- `2 Coordinated Agents`：DAG、worker、scope、merge、finalizer 和 artifact。
- `3 Evaluation Loop`：benchmark、诊断 provenance、前后对比和决策。

`Evidence details` 是追问区，包含 Run Story、Timeline、Benchmark、Diagnosis 和 Efficiency。
Debugger 看动态因果；Workbench 看最终留下的可验证 Evidence。两者缺一不可。

## 可选能力，不进入首次学习主线

- `NanoHarness Operator Console`：需要体验真实 DeepSeek、交互输入和 continuation 时使用。
- `forge bench campaign`：扩大样本、重复次数或重跑 official evaluator 时才使用。
- Workbench 前端、JSON 文件适配器、Windows setup、通用 renderer：会用即可，不要求讲实现。

面试前的完成条件不是“会全部模块”，而是三条主线和三个设计决策能被连续追问三层。
