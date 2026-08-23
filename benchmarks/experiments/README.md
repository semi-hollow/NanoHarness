# NanoHarness 实验

当前目录只保留仍用于工程复盘和技术说明的实验。每轮实验都把计划、运行索引、
机器结果和解释报告放在同一个文件夹，避免在多个目录间查找。

## 文件怎么找

```text
experiments/
├── README.md
├── tool-aci-runner-v1.json       # 可复用运行配置
├── artifact-provenance.json      # 文件由谁、在什么阶段生成
├── tool-aci-r1/
│   ├── experiment.json           # Workbench 通用实验视图声明
│   ├── README.md                 # 先读：变量、结果、决策
│   ├── plan.json                 # 运行前冻结的实验规格
│   ├── r0.execution.json         # Baseline 机器产物索引
│   ├── r1.execution.json         # R1 机器产物索引
│   ├── result.json               # 确定性指标汇总
│   └── report.md                 # 逐 Case 与过程指标报告
├── tool-aci-r2/
│   ├── experiment.json
│   ├── README.md                 # 先读：R1 问题、R2 代码变化、结果
│   ├── plan.json
│   ├── r2.execution.json
│   ├── result.json
│   └── report.md
├── mini50-v1-deepseek-v4-flash/
    ├── experiment.json           # 绝对能力测量视图声明
    ├── README.md                 # 先读：扩大样本结果与发布门裁决
    ├── plan.json                 # 冻结身份与 Pass@1 规则
    ├── mini50.execution.json     # 原始机器制品索引与 hash
    ├── completion.execution.json # 两轮 infra-only 补全索引
    ├── result.json               # 机器结果投影
│   └── report.md                 # 分母、故障和准确表述
└── multi-agent-v1/
    └── mechanism-evidence.json   # deterministic mechanism，不是性能实验
```

原始 Trace、Usage、candidate Patch 和 official evaluator 产物保存在本机
`.agent_forge/archive/experiments/tool-aci-golden-20-*/`；Mini-50 原始产物保存在
`.agent_forge/runs/benchmarks/swebench-verified-mini-50/`。旧 Runtime、Ledger、模型选型与历史采样资产统一放在
[`benchmarks/archive/`](../archive/README.md)，不进入当前阅读链。

运行 `forge ui` 后，在同一个 Workbench 顶部切换到“实验对比”，即可按以下层级读取这些资产：

```text
实验方向（Tool / ACI 优化、绝对能力测量）
└── 轮次 / 测量（R0→R1、R0→R2、Mini-50）
    └── 实验概览 / 变量与实现 / 结果对比 / 具体 Case / 证据与边界
```

Workbench 不生成另一份实验结果。`experiment.json` 只声明导航、问题、变量和版本化文件位置；
分数、过程指标与 Case 转移直接读取 `result.json`，原始运行位置读取 execution index，解释层仍由
README/report 承担。未标记为 `active` 的历史实验不会平铺到主选择器。

讲逐题 gain/regression 前，先运行 `NanoHarness Benchmark - Inspect SWE-bench Case`，把
`SWE_BENCH_CASE_ID` 改成目标 Case。默认只展示 SWE-bench 原始任务字段、固定代码起点与完整
测试名称，不调用模型，不展示 test patch、gold patch 或项目侧结论。需要核对 R1/R2 outcome 时
再把 `SWE_BENCH_EXPERIMENT` 显式改为 `both`。

## 当前实验结论

| 实验 | 唯一问题 | Official resolved | 过程指标 | 决策 |
| --- | --- | ---: | --- | --- |
| [R1](tool-aci-r1/README.md) | 同时加入 rg、find_files、repo_outline、validation head/tail 是否提升正确性 | R0 `14/20` → R1 `13/20` | 搜索略降，但失败 Tool 增加 | Reject，回滚 |
| [R2](tool-aci-r2/README.md) | 去掉 repo_outline、消除文件发现职责重叠后是否无回归提升 | R0 `14/20` → R2 `14/20` | Token -33.6%，Tool -20.8% | Reject，回滚 |
| [Mini-50](mini50-v1-deepseek-v4-flash/README.md) | 固定 quality profile 在扩大样本上的能力 | **`28/50`** | 44 个 official verdict，6 Agent Empty Patch | Publish Gate PASS |
| [Multi-Agent V1 mechanism](multi-agent-v1/mechanism-evidence.json) | Planner、真实 AgentLoop、LIVE Handoff 与集成门是否闭环 | 不适用 | deterministic mechanism assertions 全部通过 | Mechanism PASS；performance NOT EVALUATED |

准确结论是：R2 的最小正交 Tool surface 在 official correctness 持平时显著减少探索开销，
但出现一项 gain 和一项 regression，没有通过 non-regression gate，因此没有进入 stable Runtime。

## 怎么运行

默认命令只校验并打印计划，不调用模型：

```bash
.venv/bin/python scripts/run_tool_aci_golden_20.py --experiment r2
```

`--import-history` 只把既有结构化产物转换成统一执行索引；`--execute` 才会启动新的付费 Case：

```bash
.venv/bin/python scripts/run_tool_aci_golden_20.py \
  --experiment r2 --variant tool-r2 --import-history
```

流水线负责冻结变量、执行或恢复、保存结构化产物；`result.json` 和 `report.md` 由独立汇总器
生成。自然语言解释可以由 Codex 辅助，但 official outcome、分母和 Patch 字节链来自机器产物。

## 口径边界

- Golden-20 是反复使用的开发集，不是 holdout 或完整 SWE-bench Verified 排行榜。
- R1/R2 都是 bundle 实验，不能把总体结果单独归因给其中某一项 Tool。
- Candidate Patch、本地验证和 official resolved 是三层不同证据。
- 当前 stable 基线是两次 Treatment 回滚后的代码；历史实现由 commit 精确恢复。
