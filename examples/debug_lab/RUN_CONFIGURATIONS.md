# NanoHarness PyCharm Run 配置

项目只维护四个共享配置。三个 Lab 同时负责学习和展示，Operator Console 负责真实交互；
不要按每项新能力继续增加按钮。

## 先记住怎么选

```text
学习代码：Lab 1 → Lab 2 → Lab 3，使用 Debug
面试展示：按主题直接 Run 对应 Lab；默认从 Lab 1 开始
真实体验：Operator Console，使用 Run
```

三个 Lab 执行完成后都会启动或复用 Workbench，并自动打开对应的 Evidence 场景。Debug 停在断点时
Evidence 尚未完成，因此浏览器会在继续执行并结束 Lab 后打开。

## 保留的四个配置

| 配置 | 用途 | 模型与成本 | 结束后的结果 | 使用方式 |
| --- | --- | --- | --- | --- |
| `NanoHarness Lab 1 - Governed Repair` | 观察工具意图、写审批、Operation Ledger、checkpoint、continuation 和 pytest 验证 | 确定性模型；不需要 API Key，不产生模型费用 | 发布单 Agent Run Evidence，打开 Workbench `1 Governed Run` | **必学**，用 Debug 看状态变化 |
| `NanoHarness Lab 2 - Coordinated Agents` | 观察 DAG、两个隔离 worktree worker、scope gate、diff 合并和 finalizer | 确定性模型；不需要 API Key，不产生模型费用 | 发布 Fanout Evidence，打开 Workbench `2 Coordinated Agents` | **必学**，用 Debug 看并发与合并 |
| `NanoHarness Lab 3 - Evaluation Loop` | 回放 campaign，观察分层 correctness、Failure Taxonomy、before/after 和改进决策 | 使用已保存 Evidence；不调用模型、不运行 Docker | 更新 Improvement Record，打开 Workbench `3 Evaluation Loop` | **必学**，用 Debug 学评测闭环 |
| `NanoHarness Operator Console` | 用真实 DeepSeek 驱动 TUI，现场输入回答、审批写操作并自动 continuation | 需要 DeepSeek API Key，会产生少量模型费用 | 发布真实 Live Run Evidence；需要时再打开 Workbench 查看 | **可选**，用于真实产品效果，不进入首次代码主线 |

## 每个配置的代码入口

| 配置 | 外围入口 | 主要 owner |
| --- | --- | --- |
| Lab 1 | `examples.debug_lab.run.run_governed` | `Harness.run`、`AgentLoop.run`、`ToolExecutionPipeline._execute_call` |
| Lab 2 | `examples.debug_lab.run.run_coordinated` | `LiveFanoutCoordinator.run`、`LocalAgentWorkerAdapter.run_worker` |
| Lab 3 | `examples.debug_lab.run.run_evaluation` | `write_improvement_record`、Workbench Evaluation view |
| Operator Console | `examples.operator_console.main` | `forge console` 与正式 AgentLoop |

PyCharm 可能额外显示 `Current File`。它是 IDE 根据当前文件临时生成的内置动作，不属于
NanoHarness，无需删除或学习。

## 维护约束

1. 共享配置只能放在项目根目录 `.run/`，当前数量固定为四个。
2. 新能力优先接入现有三个 Lab，不为单个 feature 新增 Run 配置。
3. Lab 必须使用正式 Runtime 主链；fixture 只能固定输入和隔离 workspace。
4. 学习 Lab 默认不调用在线模型，确保可以反复 Debug。
5. 新增或删除配置时，必须同步更新本文件和 `tests/test_debug_lab_support.py` 的精确集合断言。
