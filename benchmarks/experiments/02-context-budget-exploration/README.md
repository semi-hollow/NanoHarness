# Context Budget 探索实验

## 实验身份

- 日期：2026-08-10—11
- 问题：调整 prompt token budget 是否能以更低成本维持或改善 Patch 形成能力？
- 模型：`deepseek/deepseek-v4-flash`。
- 阶段：Pre-R0 / P0—P2，属于协议成型前的探索。

## 三轮观察

| 轮次 | 样本 | 主要变化 | Candidate Patch | Provider tokens | 观察与决策 |
| --- | --- | --- | ---: | ---: | --- |
| P0 | Golden-10 | `max_prompt_tokens=65536` | 5 | 3,041,338 | 建立原始执行与成本观察 |
| P1 | Sentinel-4 | `max_prompt_tokens=32768` | 3 | 1,066,854 | Token 下降，但 SymPy 从源码修复退化为临时调试测试；拒绝 |
| P2 | Golden-10 | `max_prompt_tokens=49152` | 8 | 2,641,315 | 当时只有 1 resolved、2 unresolved、7 未裁决；旧 accepted 标签撤回 |

## 为什么不能把它写成能力提升

1. official 裁决不完整，Patch 数不等于解决数。
2. P0/P1/P2 混用了 Golden-10 和 Sentinel-4，分母不可直接比较。
3. 当时数据身份、Case 完整性、official environment 和 Patch 对齐门禁还在补齐。
4. P1 的 SymPy 退化证明，聚合 Token/Patch 指标可能掩盖错误的语义方向。

## 决策

**整体降级为探索性证据，不保留任何 accepted Treatment。** 这轮最重要的产出不是某个 token
阈值，而是把后续评测规则改成：`official resolved / planned` 为主指标，candidate、local validation、
Token、cost 和 tool failure 只作辅助证据。

## 后续留下的工程能力

- requested Case 缺失时 fail-fast；
- Case ID、数据版本、Patch SHA、Trace、Usage 与 official report 对齐；
- provider/evaluator infrastructure failure 与 patch correctness 分开；
- 新 Patch 不继承旧 Patch 的裁决。

## 证据定位

- 历史实验总记录：`3a96f14403419a85d1662d501291c75c799237c6:benchmarks/runtime-quality/golden-10-v1.json`
- 文件 SHA-256：`17942805a56dc13d12566864d2ceb81ddb0342b598794ef9bf02c04fbf9918a9`
- 对应字段：`historical_exploration`

```bash
git show 3a96f14:benchmarks/runtime-quality/golden-10-v1.json \
  | jq '.historical_exploration'
```
