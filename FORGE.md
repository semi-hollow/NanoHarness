# NanoHarness 开发约束

## 目标顺序

NanoHarness 是面向真实代码仓库的可治理软件工程智能体与评测工作台。
决策顺序固定为：运行语义正确 > 主链唯一 > 状态变更与 Evidence 可审计 >
代码精简 > 操作与审查简单 > 扩展性 > 功能数量。

新增或保留抽象必须能说明真实 Runtime 问题、运行或评测收益、Evidence 和维护成本；
“更像框架”或“以后也许扩展”不能单独构成理由。六边形边界只在能隔离真实外部变化、
保护领域语义或支持可替换测试时保留。

## 正式入口

- Core：`forge run`、`forge inspect`、`forge demo`。
- Operator：`forge resume`。
- Advanced：`forge bench`、`forge ui`，以及 multi/fanout profile。
- Hidden/Internal：`eval`、`memory`、`skills`、`doctor`、MCP server。
- 不恢复 `report/replay`、`approve/respond`、`showcase`、`tui` 或第二个 console alias；
  相同语义分别由 `inspect`、`resume`、`demo`、`ui` 承担。

参数很多不等于入口很多。不要为参数组合新增同义 CLI。

## Runtime 与 Evaluation 事实

- `Harness.run` 是公开 Single-Agent Facade；CLI single 路径必须薄委托它。
- `AgentLoop` 是唯一 Runtime Kernel；Runtime wiring 是低层依赖装配 owner。
- Single Run 产出 `RunResult / RunManifest / RunStory`，不推断 benchmark 的 Local 或
  Official 结论。
- SWE-bench 外层依次拥有 Dataset、checkout、candidate patch、Local Validation、
  Official Evaluation 和 Scorecard；不要把三种 truth scope 合并成万能 DTO。
- `patch_generated`、`local_verified`、`official_resolved` 分别回答“有修改”、
  “指定本地测试实际通过”和“独立官方 Harness 判定解决”。
- Local Evidence 必须记录实际 validation runner。没有真实执行 pytest/unittest，
  或没有收集到测试，不得标记 `local_verified`。
- Workbench 是只读 Evidence Viewer，不拥有执行、checkpoint、permission 或状态推断。

## 核心阅读范围与文档 owner

- Runtime 核心阅读范围限定为 `Harness.run`、`AgentLoop.run`、`TurnPreparation.prepare_turn`、
  `ToolExecutionPipeline.execute_calls` 和 `RunLifecycle.finalize_run` 五个入口；工具调用实现按
  “入口控制 -> 执行决策 -> 受限执行 -> 结果与恢复”四层定位。
- Evaluation 核心范围包括执行顺序、Scorecard 和 Failure Taxonomy；JSON/HTML、
  Docker、Worktree 清理和官方报告兼容属于按需查阅的适配器细节。
- `docs/核心运行机制与代码索引.md` 拥有运行规则和机制语义；
  `docs/核心能力与代码入口.md` 拥有能力到首个 Owner 的映射；`agent_forge/README.md` 只提供 package 代码地图。
- 个人笔记、问答清单和阶段性计划不属于本公开仓库的架构契约。
- 不新增平行治理文档、重构总结或第二套注释标签。关键 owner 使用现有 Code Compass
  说明上游、下游、状态变更、Evidence 和删除影响。

## 编辑与验证

- 删除前检查 wiring、Port/Protocol、console script、动态 artifact consumer 和 re-export。
- 真死代码删除；测试 fake 放测试目录；非主链的生产 Adapter 保留并标为 Advanced，
  不伪装成 `test_only`。
- Generated artifact 只放 `.agent_forge/`；不得提交 API key、provider profile、raw trace、
  benchmark checkout 或第三方 Dataset 内容。
- 开发中优先运行与改动直接相关的定向测试；提交前统一执行 `scripts/verify.sh`。真实模型和
  Official Harness 是额外 Evidence，不得用模拟结果替代。
