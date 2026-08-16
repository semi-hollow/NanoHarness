# NanoHarness Quality Showcase

当前目录只有两个有效资产：

- [`canonical-showcase-v1.json`](canonical-showcase-v1.json)：当前已完成质量口径；
- [`swebench-verified-mini-50-v1.json`](swebench-verified-mini-50-v1.json)：已执行扩大样本测量的固定 50 题清单。

当前公开结果是单 Agent 在固定 SWE-bench Verified Mini-50 上的 Pass@1 official resolved
`28/50`（56%）。50 个 Case 全部形成可归因终态：28 resolved、16 official unresolved、
6 Agent terminal Empty Patch；最终基础设施无效槽位为 0。它证明 NanoHarness 已跑通真实仓库修复与
official evaluator 闭环，不代表完整 500 题成绩，也不隔离底座模型贡献。

原始 Run 的 `23/50` 因 provider/外部中断未通过 Final Publish Gate；补全流程只替换这些
infra-invalid 槽位，不重跑 resolved、unresolved 或 Agent Empty Patch。完整分母和补全规则见
[Mini-50 报告](../experiments/mini50-v1-deepseek-v4-flash/report.md)。

## Mini-50 运行入口

Mini-50 使用质量优先的 `opencode-go/deepseek-v4-flash`：thinking enabled、reasoning
effort max、128 steps、64K repository context chars、131,072 prompt tokens、16,384
reserved output tokens，并且没有 cost cap；超时和故障熔断仍保留，避免基础设施异常无限挂起。
Smoke Gate 固定使用不属于 Mini-50 的 `django__django-11451` 与
`sphinx-doc__sphinx-10323`，只验证模型、Tool、Patch、official evaluator 和证据链是否
完整，不以题目是否 resolved 作为门禁。

每题独立 checkpoint。恢复时只继续尚未开始的题：已经 terminal 的题幂等跳过；已经启动
但未 terminal 的题绝不从头重跑，而是 fail closed 并使 campaign 保持 incomplete。默认命令
会机械落盘 `frozen_plan.json`，但不会调用模型：

```bash
.venv/bin/python scripts/run_swebench_verified_mini_50.py
```

显式执行：

```bash
NANOHARNESS_ROOT=/absolute/path/to/NanoHarness \
  zsh -lic 'cd "$NANOHARNESS_ROOT" && .venv/bin/python scripts/run_swebench_verified_mini_50.py --execute'
```

运行产物位于 `.agent_forge/runs/benchmarks/swebench-verified-mini-50*/`。最终
`campaign_summary.json` 与 `campaign.md` 会报告 official resolved / 50、Wilson 95% 区间、
empty Patch、基础设施失败、Token、成本、Tool failure 与逐题索引。

公开结果另受 `final_publish_gate.json` 约束：仅当 planned/terminal-accounted 均为 50、
provider/runtime/evaluator infra 均为 0，且 source/config/cohort/model 与 frozen plan 完全一致时，
才允许发布正式 `X/50`；Empty Patch 会作为未解决结果保留在 50 题分母中。

旧 Canonical-50、quality-selection 和 Smoke-5 资产已移入 [历史归档](../archive/README.md)，
不参与当前展示。
