# NanoHarness Quality Showcase

当前目录只有两个有效资产：

- [`canonical-showcase-v1.json`](canonical-showcase-v1.json)：当前已完成质量口径；
- [`swebench-verified-mini-50-v1.json`](swebench-verified-mini-50-v1.json)：下一次正式测量的固定 50 题清单。

当前公开结果是单 Agent 在固定 SWE-bench Verified 开发样本上的 Pass@1 official resolved
`4/10`（约 40%）。它证明 NanoHarness 已跑通真实仓库修复与 official evaluator 闭环，不代表
完整 500 题成绩，也不隔离底座模型贡献。

## Mini-50 运行入口

Mini-50 使用质量优先的 `opencode-go/deepseek-v4-pro`、128 steps、无 token/cost 质量上限，
每题独立 checkpoint。默认命令只校验计划：

```bash
.venv/bin/python scripts/run_swebench_verified_mini_50.py
```

显式执行：

```bash
NANOHARNESS_ROOT=/absolute/path/to/NanoHarness \
  zsh -lic 'cd "$NANOHARNESS_ROOT" && .venv/bin/python scripts/run_swebench_verified_mini_50.py --execute'
```

运行产物位于 `.agent_forge/evaluations/swebench-verified-mini-50/`。最终
`campaign_summary.json` 与 `campaign.md` 会报告 official resolved / 50、Wilson 95% 区间、
empty Patch、基础设施失败、Token、成本、Tool failure 与逐题索引。

旧 Canonical-50、quality-selection 和 Smoke-5 资产已移入 [历史归档](../archive/README.md)，
不参与当前展示。
