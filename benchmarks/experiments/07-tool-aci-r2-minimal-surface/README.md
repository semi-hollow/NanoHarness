# Tool / ACI R2 最小工具面实验

## 实验身份

- 日期：2026-08-13—14
- 问题：R1 的过程指标变好、official correctness 回退后，缩小 Tool surface 能否在固定
  Golden-20 开发集上带来无回归的 correctness 提升？
- 模型：`opencode-go/deepseek-v4-flash`。
- 样本：与 R0/R1 完全相同的固定 Golden-20；R2 设计前已经看过历史 outcome，因此明确属于
  development iteration，不是 holdout。
- Baseline source：`0ae0d9ae9444d723e319fc0c7eadec9b631d374c`。
- R2 Treatment：`563a99fe72b078fa91bfb682d60d6d19f398a864`。
- Frozen run source：`d7fc8110f9ec6bde7f7f794fb06f25986d279448`。

## R2 相比 R0 的单一 bundle

1. `grep_search` 保持 model-visible schema，backend 改为有界 `rg`；
2. `find_files` 使用 `rg --files`，并在 SWE task-aware 路由中替代 `list_files`；
3. Validation 长输出统一有界，截断时同时保留 head 与 tail；
4. 不引入 R1 的 `repo_outline`，不改变 selected-file preview 权重；
5. `apply_patch`、LSP、Code Graph、Memory、Multi-Agent 和 Prompt 改造全部 defer。

## 结果

| 指标 | Tool-R0 | Tool-R2 | Δ |
| --- | ---: | ---: | ---: |
| Official resolved | **14/20 (70%)** | **14/20 (70%)** | 0 |
| LLM calls | 486 | 377 | -109 (-22.4%) |
| Total tokens | 18,800,473 | 12,486,369 | -6,314,104 (-33.6%) |
| Tool calls | 673 | 533 | -140 (-20.8%) |
| Search calls | 261 | 207 | -54 (-20.7%) |
| Read calls | 217 | 173 | -44 (-20.3%) |
| Failed tool calls | 45 | 39 | -6 |
| Failed validations | 35 | 28 | -7 |
| Mean tool calls before first edit | 15.95 | 13.45 | -2.50 |

逐题迁移为 13 个 resolved→resolved、5 个 unresolved→unresolved、1 个 unresolved→resolved、
1 个 resolved→unresolved：`sympy__sympy-20590` 获益，`astropy__astropy-14182` 回归。
McNemar exact two-sided `p=1.0`。

Treatment 被真实激活：`find_files` 在 377 个 Context 中可见、调用 45 次；`list_files` 完全不可见且
调用 0 次；validation output window 观察 66 次，其中 9 次真实 head/tail 截断；`repo_outline` 为 0。

## 决策

**Reject，并回滚。** R2 的执行效率信号明确，但 official correctness 没有净提升且出现一项回归，
不满足预注册的 non-regression gate。R2 Treatment 由 `92f4de56a1391b58e8e249471ebd4ec04102f60b` 回滚，stable Runtime 不保留该
bundle；实验 commit、协议和结果继续保留以便审计。

这个结果支持的准确结论是：**更正交、职责更清晰的 Tool surface 能显著减少探索和资源消耗，但本轮
没有证明它提高任务解决率。** 过程效率不能替代 official correctness。

## 声明边界

- Golden-20 是反复使用的开发/回归集，不是 holdout 或完整 SWE-bench Verified 结果。
- 本轮评估三部分最小 bundle，不能把得失单独归因于某一个组件。
- 两侧 20/20 均核验 candidate、prediction 与 official Patch 字节链；official 分母完整，0 infra、
  0 fallback。
- Agent generation 是一次 Pass@1，没有 correctness 重跑；R2 全部使用 OpenCode Go / V4 Flash，
  没有因额度切换 provider。

## 证据定位

- [冻结协议](../../regression/tool-aci-golden-20-r2-protocol.json)
- [完整机器结果](../../regression/tool-aci-golden-20-r2-result.json)
- [人类可读报告](../../regression/tool-aci-golden-20-r2-report.md)
- [安全汇总脚本](../../../scripts/summarize_tool_aci_golden_20_r2.py)
- Prerun source tag：`tool-aci-golden-20-r2-prerun-20260813`
- Protocol SHA-256：`b212a817bc92783dfcca311f23a0697b84b5947ba45ad0142d659ffa4d4777bd`
- Result SHA-256：`c381ee37dff58c6d46fd8f52a525c9bcb34772e31091a30f059150692c9897e5`
- Report SHA-256：`018a84d1779cb62bcfb12cb41efff69495413d2d18b873383d5332631392544a`
- 本机完整原始证据：`.agent_forge/tool-aci-golden-20-r2/`。
