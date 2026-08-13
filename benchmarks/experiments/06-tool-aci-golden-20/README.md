# Tool / ACI Golden-20 成对实验

## 实验身份

- 日期：2026-08-13
- 问题：在固定模型、Case、预算、Runtime 和 official evaluator 下，轻量 Tool / ACI bundle 是否
  提高 official resolved？
- 模型：`opencode-go/deepseek-v4-flash`。
- 样本：固定 Golden-20；前 10 题为 seen regression，后 10 题为 outcome-blind fresh extension。
- Baseline source：`0ae0d9ae9444d723e319fc0c7eadec9b631d374c`。
- Treatment：`296000864d6a2c1476c28b790f030b0ffc4cca5b`。

## Treatment 范围

1. `grep_search` 改为 `rg` backend；
2. 新增由 `rg --files` 支撑的 `find_files`；
3. 为已排序 Python 文件加入有界 AST `repo_outline`；
4. Validation 长输出保留显式 head/tail；
5. `apply_patch`、LSP、Code Graph、Memory、Multi-Agent 与 Prompt 改造全部 defer。

## 结果

| 指标 | Tool-R0 | Tool-R1 | Δ |
| --- | ---: | ---: | ---: |
| Official resolved | **14/20 (70%)** | **13/20 (65%)** | -1 / -5pp |
| Seen regression | 7/10 | 7/10 | 0 |
| Fresh extension | 7/10 | 6/10 | -1 |
| LLM calls | 486 | 475 | -11 |
| Total tokens | 18,800,473 | 18,915,188 | +114,715 |
| Tool calls | 673 | 656 | -17 |
| Search calls | 261 | 243 | -18 |
| Failed validations | 35 | 30 | -5 |
| Failed tool calls | 45 | 55 | +10 |

逐题迁移为 12 个 resolved→resolved、5 个 unresolved→unresolved、1 个 unresolved→resolved、
2 个 resolved→unresolved。McNemar exact two-sided `p=1.0`。

Treatment 被真实激活：`find_files` 调用 30 次，`repo_outline` 进入 Context 362 次，validation
head/tail 触发 14 次。过程指标变化没有转化为 correctness uplift。

## 决策

**Reject。** Treatment 由 `a79d71051e0b968df81e5cc0f0851d434e89f358` 回滚；stable master 不保留
该 bundle。实验协议、汇总器、结果和 Treatment commit 继续保留以便审计。

若继续这个方向，应在新协议中拆开组件逐一验证；不能对同一 Golden-20 做结果驱动重跑。

## 声明边界

- Golden-20 是固定开发集，不是 holdout 或完整 SWE-bench Verified 结果。
- 这轮评估整个 Tool / ACI bundle，不能把得失归因于任一单独组件。
- 20/20 两侧均核验 candidate、prediction 与 official Patch 字节链；未读取 gold 或逐测试输出。
- R0/R1 分别观察到 3/5 个 provider retried calls，均无 fallback；每个 Case 的 Agent generation
  仍是一次 Pass@1。

## 证据定位

- [冻结协议](../../regression/tool-aci-golden-20-v1.json)
- [完整机器结果](../../regression/tool-aci-golden-20-v1-result.json)
- [人类可读报告](../../regression/tool-aci-golden-20-v1-report.md)
- [安全汇总脚本](../../../scripts/summarize_tool_aci_golden_20.py)
- Manifest SHA-256：`fec7c409ce030837165b9431264822a8e0ebddb418fecf4540d8434865f20bbe`
- Result SHA-256：`11c2f4af6ba946c0e66723a730d2700ffa893d37c5d21e124d9a215f9b58d7e7`
- Report SHA-256：`bec5cb93984e90fb31d106a131441d2a6fc26f81cc35859359c38ad7a37330ad`
