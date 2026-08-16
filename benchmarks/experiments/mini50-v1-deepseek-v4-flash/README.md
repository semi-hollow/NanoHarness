# SWE-bench Verified Mini-50 · DeepSeek V4 Flash

本目录记录 NanoHarness 在固定 50 题、固定 Runtime 与固定质量配置上的正式扩大样本测量。
最终结果是 **28/50 official resolved（56.0%）**；50 个 Case 都有可归因的 Agent 能力终态，
Final Publish Gate 通过。

## 实验身份

- 日期：2026-08-16
- 数据集：`princeton-nlp/SWE-bench_Verified@test@c104f840...`
- 样本：`swebench-verified-mini-50-hal-v1`，50 个固定 Case
- source：`3ec537113a26491b7b7a51e323a3d3af40f4754f`
- provider/model：`opencode-go/deepseek-v4-flash`
- profile：thinking enabled、reasoning max、128 steps、64K repository context chars、
  131,072 prompt tokens、16,384 reserved output tokens、无 cost cap
- 口径：correctness Pass@1；只有没有形成可用能力终态的 provider/外部中断允许按冻结名单补全，
  resolved、official unresolved 和 Agent terminal Empty Patch 均不可重跑

## 最终结果

| 指标 | 结果 |
| --- | ---: |
| Planned / terminal accounted | 50 / 50 |
| Official resolved | **28/50（56.0%）** |
| Wilson 95% CI | 42.3%–68.8% |
| Official unresolved | 16 |
| Agent terminal Empty Patch | 6 |
| Provider / Runtime / Evaluator / 外部中断 | 0 / 0 / 0 / 0 |
| 非空 Patch 字节链 | 44/44 一致 |

## 为什么存在补全轮次

原始 Run 完成了固定 50 个槽位，但只有 40 个槽位形成可归因的能力结果：23 resolved、
12 official unresolved、5 Agent terminal Empty Patch；另有 8 个 provider transport failure 和
2 个外部手动中断。原始观察值 `23/50` 因基础设施发布门失败，没有被发布为正式成绩。

随后只按失败类型补全，不看 correctness outcome：

1. v1.1 仅运行上述 10 个基础设施无效 Case，得到 4 resolved、4 unresolved、1 Agent Empty
   Patch 和 1 个新的 provider failure；其余 40 个结果保持不可变。
2. v1.2 仅运行 v1.1 中仍受 provider 影响的 `sympy__sympy-12481`，得到 official resolved；
   其余 49 个结果保持不可变。

因此完整执行一共包含 61 个启动，但最终 50 题各自只保留一个有效能力轨迹。它不是“看到没通过就重跑”，
也不是把 61 次尝试挑最好的 50 次；补全资格只来自 provider/外部中断，所有真实 Agent 失败都保留。

## 文件定位

- [`plan.json`](plan.json)：原始 Mini-50 的冻结计划
- [`mini50.execution.json`](mini50.execution.json)：原始运行机器制品索引
- [`completion.execution.json`](completion.execution.json)：两轮基础设施补全的选择、制品与 SHA-256
- [`result.json`](result.json)：最终机器结果投影与逐类 Case ID
- [`report.md`](report.md)：逐 Case 结果、Empty Patch 诊断与准确表述
- 原始 Trace、Usage、candidate Patch、prediction 与 official evaluator：
  `.agent_forge/runs/benchmarks/swebench-verified-mini-50*/`

Workbench 可读取任意原生 Single-Run Trace。选择“最近一次 Runtime 运行”后，“上下文与决策”会显示
完整 Tool 参数、Observation 和连续相同 Tool+参数序列；“执行过程”显示停止原因。它展示可审计行为，
不伪造或暴露隐藏思维链。
