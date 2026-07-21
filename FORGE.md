# NanoHarness 开发约束

## 目标顺序

NanoHarness 是用于三年以上 AI Agent 开发岗位面试的 Coding Agent Runtime 项目。
决策顺序固定为：维护者能彻底掌握 > 主链唯一 > 状态、副作用与 Evidence
正确 > 代码精简 > 演示简单 > 扩展性 > 功能数量。

新增或保留抽象必须能说明真实 Runtime 问题、面试回报、Evidence 和学习成本；
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

## 理解预算与文档 owner

- 第一遍 Runtime Core 固定为 `docs/ARCHITECTURE.md` 中的 12 个文件。
- Evaluation 是第二条学习线，只掌握执行顺序、Scorecard、Ablation 和 Failure Taxonomy；
  JSON/HTML、Docker、Worktree 清理和官方报告兼容细节退出首轮阅读。
- `docs/ARCHITECTURE.md` 是唯一生产架构契约；Facade Catalog 是唯一入口总表；
  学习顺序、闭卷题和演示脚本由 NanoHarness-Study-Notes 持有。
- 不新增平行治理文档、重构总结或第二套注释标签。关键 owner 使用现有 Code Compass
  说明上游、下游、状态、副作用、Evidence 和删除影响。

## 编辑与验证

- 删除前检查 wiring、Port/Protocol、console script、动态 artifact consumer 和 re-export。
- 真死代码删除；测试 fake 放测试目录；低面试回报的生产 Adapter 保留并标为 Advanced，
  不伪装成 `test_only`。
- Generated artifact 只放 `.agent_forge/`；不得提交 API key、provider profile、raw trace、
  benchmark checkout 或第三方 Dataset 内容。
- 开发中优先运行与改动直接相关的定向测试。Windows 最小检查用
  `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`；真实模型和 Official Harness
  是额外 Evidence，不得用模拟结果替代。
