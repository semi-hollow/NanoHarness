# NanoHarness Debug Lab：从动态过程掌握 Agent Runtime

> 这是 Study Notes 的 A1，也是唯一动态学习入口。学习总顺序仍只认
> [NanoHarness Study Notes](https://github.com/semi-hollow/NanoHarness-Study-Notes)。

Debug Lab 解决的不是“命令记不住”，而是“只看过概念，没有控制过一次真实运行”。它固定输入、
自动创建干净 workspace、自动发布 Evidence，并预装关键断点。你只需要按 1 → 4 点击 Debug，
不用复制路径、恢复 fixture、导入 JSON 或手工配置十几个断点。

它不是第二套 Runtime，也不是产品功能。四个实验最终都进入正式 `Harness`、`AgentLoop`、工具和
Evidence 实现；学习脚本只负责准备固定输入。

## 第一次只做一次

先关闭 PyCharm，然后在 macOS Terminal 进入 `NanoHarness` 根目录，执行：

```bash
scripts/setup_macos_local.sh --quick
```

该命令创建 `.venv`、安装开发依赖、执行 `forge doctor`，并按源码 symbol 自动安装 20 个
`NanoHarness Debug Lab` 断点。随后用 PyCharm **单独打开 NanoHarness 根目录**，不要打开两个
repo 的共同父目录；否则 `$PROJECT_DIR$` 会指错。

如果首次 setup 是在已经打开的 PyCharm 中执行，环境仍会准备好，但断点安装会被安全跳过。
关闭 PyCharm 后只需再执行一次：

```bash
.venv/bin/python scripts/install_pycharm_debug_lab.py
```

这是唯一一次断点准备；不需要逐个点击代码行。脚本只维护同名 breakpoint group，保留你的其他
断点，并根据 symbol 重新计算源码行号。

## 从上到下点四次 Debug

在 PyCharm 顶部运行配置列表中按顺序选择，点击甲虫形 Debug 按钮：

| 顺序 | 共享配置 | 固定输入 | 这次必须看懂 |
| --- | --- | --- | --- |
| 1 | `NanoHarness Lab 1 - Control Plane` | 固定写操作 | approval、operation identity、checkpoint、continuation |
| 2 | `NanoHarness Lab 2 - Fixed Repair` | 固定错误 calculator | model intent → tool → patch → 真实 pytest → Evidence |
| 3 | `NanoHarness Lab 3 - Live Agent` | 与 Lab 2 完全相同 | DeepSeek 如何选择工具、失败、恢复或完成 |
| 4 | `NanoHarness Lab 4 - Astropy Evidence` | `astropy__astropy-12907` | candidate / local / official 三层证据 |

断点停住后先看 Variables，再用 Resume Program 到下一个断点。第一遍不要逐行 Step Into
第三方库，也不要研究 JSON 读写。

## Lab 1：先掌握控制面

运行轨迹固定为：

```text
Harness.run
  → AgentLoop.run
  → ToolExecutionPipeline._execute_call
  → OperationTracker.describe
  → ToolAuthorizationGate._resolve_approval
  → waiting_approval checkpoint
  → explicit decision
  → continuation
  → completed
```

重点观察：

- 工具请求只是模型意图；真正能否执行由 Runtime 决定。
- 审批绑定 `operation_key`，不是“相信某段自然语言”。
- continuation 加载持久化状态，不是恢复 Python 调用栈。
- `RunLifecycle.stop` 把最终状态和 artifact 落盘。

## Lab 2：固定模型变量，观察真实 Runtime

每次运行都会复制同一个错误仓库：`calculator.py` 把加法写成减法。确定性 ModelPort 只固定四个
工具意图：读源码、读测试、改一行、执行 `pytest`。文件工具、权限、状态机、pytest 子进程、
trace 和 Run Story 都是生产实现。

在 `DiagnosticsTool.execute` 停住时看 `kind=pytest` 与 `target=test_calculator.py`；继续后在
Timeline 中找 `validation_evidence`。这个 Lab 证明真实 focused test 被执行，但不是 SWE-bench
official resolved。

## Lab 3：换成真实 DeepSeek，输入保持不变

第一次点击会弹出 macOS 隐藏输入框；API key 保存到 Keychain，之后自动读取，不写入 repo、
配置或 artifact。Lab 3 与 Lab 2 的 task、bug 和测试相同，因此差异主要来自真实模型决策。
两者还固定为同一组 `read_file / apply_patch / diagnostics` 工具，并关闭 Skill 与 Memory recall；
这样比较的是模型决策，而不是额外能力带来的变量。

重点比较 `_call_model` 前后的 messages、可见 tool schemas、返回 ToolCall，以及 Runtime 如何处理
无效参数、重复动作和预算。不要期待每次轨迹完全相同；随机性本身就是要观察的对象。

## Lab 4：只学评测口径，不钻基础设施

Lab 4 第一次会自动准备固定 revision 的官方 SWE-bench Harness，需要已经启动的
Docker-compatible runtime（Docker Desktop 或 Colima）。Apple Silicon 还要确保该 runtime 能执行
`linux/amd64` 官方镜像。当前 Lab 给 Agent 16 步预算，但仍只报告实际 artifact，不承诺必然有 patch。
官方环境准备可能较慢，但不属于核心阅读范围。

只跟住八步：

1. `SwebenchCaseSource.load` 按 `instance_id` 取 case。
2. `SwebenchWorkspaceManager.prepare` checkout `base_commit`；Agent 只看 issue 与代码。
3. `LocalCaseExecutor.run` 装配并驱动同一 Runtime。
4. `Candidate patch` 停点先观察 `patch/final_answer`，Step Over 一行后再看 `status`；这里只证明
   生成了候选 diff。
5. `read_local_validation` 从 trace 投影 Agent 实际执行过的 focused test 证据。
6. `SwebenchOfficialEvaluator.evaluate` 调独立官方 Oracle。
7. `parse_official_results → apply_official_results` 解析并写回 per-case official outcome。
8. `DiagnoseBenchCase.attach → FileBenchArtifacts.publish_run` 在最终事实之后分类并发布报告。

面试结论固定为：`official_resolved` 判断最终结果；`patch_generated` 与 `local_verified` 定位失败
发生在哪一层。Local Passed 不能替代 Official Resolved。

## 断点里看哪些对象

| 断点 owner | 先看对象 | 回答的问题 |
| --- | --- | --- |
| `Harness.run` | 入口已有 `request`、`self._config`、`self._extensions` | 外部输入怎样收敛到单一 Public API |
| `AgentLoop.run` | 入口看 `task/agent_name`；Step Over 一行后看 `session` | 单次 run 的状态容器怎样创建 |
| `TurnPreparation.execute` | 入口看 `session/step/force_compaction` | 哪个 turn 正在准备；结果会作为下一断点的 `turn` |
| `AgentLoop._call_model` | `turn.messages_for_llm`、`turn.schemas`、预算字段 | 模型这次真正看见什么；Step Over 模型调用后才有 `response` |
| `ToolExecutionPipeline._execute_call` | `tool_call`、`allowed_tool_names`、`session.tool_history` | 意图是否可见、重复；Step Over 到 `intent = ...` 后看治理身份 |
| `OperationTracker.describe` | 入口看 `tool_call`、`self.config.workspace` | Step Out 回调用方后看返回的 `intent.key/fingerprint` |
| `ToolAuthorizationGate._resolve_approval` | `intent`、`reason`、`self.config.auto_approve_writes` | 具体 operation 为什么允许或暂停 |
| `DiagnosticsTool.execute` | 入口看 `arguments`；Step Over 两行后看 `kind/target` | Step Into `_pytest` 才看最终 argv 与子进程 |
| `RunLifecycle.stop` | `request`、`self.checkpoint`；执行 hook 后看 `effective` | 状态与 Evidence 何时持久化 |
| `RunSwebench.execute` | 入口看 `request/run_id/layout`；Step Over 三行后看 `cases/summary` | Benchmark 怎样编排而不定义真相 |
| `SwebenchCaseSource.load` | `request.instance_ids/dataset_name/split`；返回前看 `cases` | 固定 case 如何从 dataset 边界进入系统 |
| `LocalCaseExecutor.run` | 入口看 `case/case_dir/agent_mode/request`；Step Over 后看 `workspace` | 单题如何进入正式 Agent 主链 |
| `SwebenchWorkspaceManager.prepare` | `case.repo/base_commit/instance_id`；返回前看 `workspace` | 隔离 worktree 是否确实位于指定 revision |
| `Candidate patch` | 先看 `patch/final_answer`；Step Over 一行后看 `status`，并展开 `active_workspace` | Runtime 结果怎样变成 candidate diff，而不是 solved claim |
| `read_local_validation` | 入口看 `trace_path`；循环后看 `records/statuses` | Local Verified 从哪些 trace event 投影 |
| `SwebenchOfficialEvaluator.evaluate` | 入口看 `summary/request`；构造后看 `command`，子进程后看 `parsed` | 独立 Oracle 怎样给最终结论 |
| `parse_official_results` | `run_id/instance_ids`；返回前看 `outcomes/warnings` | aggregate/per-case JSON 怎样归一化成显式 outcome |
| `apply_official_results` | `case_results/parsed/process_exit_code`；循环后看每个 result | Official 事实何时写回，不由进程退出码猜测 |
| `DiagnoseBenchCase.attach` | 入口看已有 local/official 字段；Step Over 后看 `diagnosis` | taxonomy 如何基于最终证据选择唯一分类 |
| `FileBenchArtifacts.publish_run` | `summary.case_results`，尤其 official/failure 字段 | report 是否只消费最终事实并在最后发布 |

## Debugger 与 Workbench 不冲突

两者看的是同一次运行，但回答不同问题：

- Debugger 看活的内存和因果过程：为什么下一步这样走。
- Workbench 只读回放落盘 Evidence：最终留下什么、能证明到哪一层。

每个 Lab 结束后都更新 `.agent_forge/latest`。要回放刚才的结果，双击
`scripts/start_workbench.command`；Fixed Lab 的 pytest 结果重点看 Timeline 的
`validation_evidence`，Astropy 的 Local/Official 结果看 Benchmark 与 Claim Ladder。

## 公开动作只记这六个

Debug Lab 帮你掌握过程；下面只是 Facade 地图，不再为每个命令维护一套教程：

| 动作 | owner | 什么时候用 |
| --- | --- | --- |
| `forge run` | CLI → `Harness.run` | 对一个 repository task 发起真实运行 |
| `forge inspect` | Inspection → Run Story | 只读定位 run、artifact 或源码 symbol |
| `forge demo` | Showcase → `Harness.run` | 确定性展示 approval/HITL 控制面 |
| `forge resume` | Operator → `Harness.run` | 给出 answer/decision 后创建显式 continuation |
| `forge bench` | Bench Facade → `RunSwebench` | 生成并评测 case Evidence |
| `forge ui` | Workbench | 只读展示同一份 Evidence，不执行 Agent |

## 面试时复用同一套能力

现场不需要手敲长命令，也不需要在 UI 再实现一个执行按钮。执行：

```bash
scripts/interview_demo.sh
```

它运行 Lab 1 的正式控制面并打开只读 Workbench。想展示真实模型时才用：

```bash
scripts/interview_demo.sh --live
```

如果 Lab 3/4 已提前完成，下面两个入口只切换到已保存 Evidence，不调用模型、不重跑 Docker：

```bash
scripts/interview_demo.sh --show-live
```

```bash
scripts/interview_demo.sh --show-astropy
```

讲解顺序固定为：`Run Story → Timeline → Approval/Checkpoint → Artifacts → Claim Ladder`。
Astropy official Evidence 建议提前由 Lab 4 生成并保留，不在短面试里临时等待 Docker。

## 到哪里就停止

完成 A1 的标准不是记住全部命令，而是你能在断点处解释：模型看见什么、ToolCall 谁执行、
为什么暂停、pytest 谁启动、状态写到哪里、Local 与 Official 为什么不能混用。完成后立即回到
Study Notes 的 A2，不继续研究 SWE-bench Docker 脚本、通用 JSON 读写或 Workbench 前端渲染。
