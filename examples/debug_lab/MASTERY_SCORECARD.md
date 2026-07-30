# NanoHarness 面试掌握度验收卡

这不是第四份教程，也不是功能清单。它只回答一个问题：

> 我能否不依赖文档，把 NanoHarness 的三个核心设计讲清楚、跑出来并用证据自证？

运行方法和代码入口统一看 [README.md](README.md)。本文件只用于闭卷验收，不需要修改或提交打勾记录。

## 使用规则

每次只练一个 Lab，控制在 45 分钟：

1. **运行前 5 分钟**：不看代码，先口述你预测的状态变化。
2. **Debug 20 分钟**：点击对应的 PyCharm Debug 配置，只看本卡列出的对象。
3. **Evidence 10 分钟**：打开 Workbench 对应场景，找到最终证据。
4. **闭卷 10 分钟**：关掉代码和文档，回答追问并给自己计分。

每项 `0 / 1` 分，共 18 分。满足以下条件才算达到面试门槛：

- 总分至少 **15 分**；
- 三个 Lab 的“证据”和“边界”均不得为 0；
- 能连续讲完任意一个 Lab 5 分钟，并接受至少三层追问。

达到门槛后停止加功能，开始真实投递和模拟面试。新功能只能由真实面试反馈触发。

## Lab 1：Governed Repair

点击：`NanoHarness Lab 1 - Governed Repair`

运行前必须预测：

- 第一次写请求不会直接改文件，而会停在 `waiting_approval`。
- checkpoint 保存可恢复业务状态，不保存 Python 调用栈。
- continuation 复用同一个 operation identity，写入后执行 focused pytest。

Debug 时只看：

- `session`
- `tool_call`
- `operation_intent.key`
- `operation_intent.fingerprint`
- `checkpoint`
- `validation_evidence`

Workbench：`1 Governed Run`

| 验收项 | 1 分标准 | 得分 |
| --- | --- | --- |
| 定位 | 30 秒找到 `Harness.run` 与 `ToolExecutionPipeline._execute_call` | 0 / 1 |
| 预测 | 断点前说出 `running → waiting_approval → running → completed` | 0 / 1 |
| 数据 | 解释 operation key、fingerprint、ledger、checkpoint 各自解决什么问题 | 0 / 1 |
| 故障 | 解释 crash 后为何产生 `operation_outcome_unknown`，为何不能自动重放 | 0 / 1 |
| 证据 | 在 Workbench 找到审批、checkpoint、工具结果和 pytest 证据 | 0 / 1 |
| 边界 | 明确它偏向 at-most-once，不声称 exactly-once | 0 / 1 |

闭卷追问：

1. 为什么审批绑定结构化 operation，而不是绑定“允许修改”这句自然语言？
2. 如果副作用发生后进程崩溃，系统知道操作成功了吗？
3. 为什么 ledger 不能被 checkpoint 替代？

## Lab 2：Coordinated Agents

点击：`NanoHarness Lab 2 - Coordinated Agents`

运行前必须预测：

- `pricing.py` 和 `shipping.py` 的声明写集合不相交，因此进入同一 batch。
- 两个 worker 在不同 Git worktree 中运行真实 AgentLoop。
- Coordinator 会检查实际 touched files，再合并 candidate diff；finalizer 最后运行测试。

Debug 时只看：

- `plan.tasks`
- `dependency_batches`
- `worker_workspace`
- `completed_batch_results`
- `merged_task_ids`
- `finalizer_result`

Workbench：`2 Coordinated Agents`

| 验收项 | 1 分标准 | 得分 |
| --- | --- | --- |
| 定位 | 30 秒找到 `LiveFanoutCoordinator.run` 与 `LocalAgentWorkerAdapter.run_worker` | 0 / 1 |
| 预测 | 根据 DAG 和 write scope 说出哪些任务并发、哪些任务串行 | 0 / 1 |
| 数据 | 区分声明 scope、实际 touched files、worker diff 和 integrated diff | 0 / 1 |
| 故障 | 说明 scope 越界或 merge conflict 时为何 fail closed | 0 / 1 |
| 证据 | 在 Workbench 找到 worker、batch、合并内容和 finalizer 结果 | 0 / 1 |
| 边界 | 明确它是本机受控 fanout，不声称分布式 worker service 或自动任务规划 | 0 / 1 |

闭卷追问：

1. 为什么“任务看起来互不影响”还不够，必须验证实际 touched files？
2. RPC/进程通信是不是这里的核心？Agent 之间真正交换的契约是什么？
3. 为什么 verifier/finalizer 不应和两个写 worker 一起并发？

## Lab 3：Evaluation Loop

点击：`NanoHarness Lab 3 - Evaluation Loop`

运行前必须预测：

- 该 Lab 回放已保存 campaign，不重新调用模型或 Docker evaluator。
- taxonomy 给出版本化规则诊断，人工复核是独立事实。
- official 结果持平、工具失败下降但成本上升，决策应为 `iterate`。

Debug 时只看：

- `campaign_dir`
- `summary`
- `diagnosis`
- `before_after`
- `decision`
- `claim_boundary`

Workbench：`3 Evaluation Loop`

| 验收项 | 1 分标准 | 得分 |
| --- | --- | --- |
| 定位 | 30 秒找到 `run_evaluation` 与 `write_improvement_record` | 0 / 1 |
| 预测 | 运行前说出 official、tool failure、token 和 cost 的变化方向 | 0 / 1 |
| 数据 | 区分 candidate patch、local validation、official evaluation | 0 / 1 |
| 故障 | 说明 evaluator error 为什么不能归类为 patch rejected | 0 / 1 |
| 证据 | 在 Workbench 找到 problem、hypothesis、before/after、decision | 0 / 1 |
| 边界 | 明确两个 post-hoc case 只支持 commissioning evidence | 0 / 1 |

闭卷追问：

1. 数据飞轮为什么不是“每次失败自动新增一个 UT”？
2. 为什么失败工具调用减少不等于 Agent correctness 提升？
3. 什么时候可以从 `iterate` 改成 `adopt`，还缺什么实验？

## 面试前最后验收

不看仓库，拿白纸画出三条链：

```text
Tool intent → policy → approval → checkpoint → continuation → evidence

DAG → conflict-free batch → isolated workers → scope gate → merge → finalizer

Run evidence → taxonomy → human review → hypothesis → paired comparison → decision
```

然后分别回答：

1. **动机**：真实问题是什么？
2. **备选**：最简单的替代方案是什么？
3. **选择**：为什么采用当前设计？
4. **失败**：它曾经在哪个场景出错？
5. **证据**：哪个 artifact 或测试证明修复有效？
6. **边界**：当前实现不能声称什么？

任何一条答不出来，只回到对应 Lab，不扩展新的项目能力。

## 可以跳过的代码

首次面试准备中只需知道用途，不需要精读：

- Workbench HTML/CSS renderer；
- JSON Repository 的序列化细节；
- CLI 参数解析与 PyCharm XML；
- Windows 安装脚本；
- MCP、web tool 和非 Coding preset；
- 测试 fixture 与展示 wrapper。

这些属于 Adapter 或展示基础设施，不是三个核心决策的 owner。面试官追问时可以定位，但不要把有限
学习时间投入其内部实现。
