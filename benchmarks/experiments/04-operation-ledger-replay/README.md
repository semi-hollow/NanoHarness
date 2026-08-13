# Operation Ledger Restored-Precondition Replay

## 实验身份

- 日期：2026-08-12
- 问题：同一 Run 内，已执行操作的目标状态被验证/恢复过程精确还原到 precondition 后，允许一次
  有界重放，能否保留模型已经形成的源码修复，同时不破坏既有正确 Case？
- 模型：`opencode-go/glm-5.2`。
- Treatment：same-run restored-precondition one-shot replay。
- Treatment commit：`485ba920df516f0e5c6e5eefd10d8d5d9325ed9a`。

状态契约只开放一个分支：same run、prior executed 恰一次、fingerprint 完整且
`current == pre != post` 时允许重放一次。post-state 仍幂等跳过；cross-run、未知漂移、缺指纹和第三次
重放继续 fail closed。

## 分阶段结果

### Case-level Target

- Target：`pytest-dev__pytest-8399`，事后从历史失败中选取。
- 历史 Baseline：0/2 official resolved。
- Fresh Treatment：2/2 official resolved。
- 机制 marker：2/2 直接激活。
- 两次均有 product-source Patch，candidate/prediction/official Patch 字节对齐。
- Local validation：0/2 passed；正向证据来自 official evaluator，不应把 local 状态说成通过。

### Frozen Guards

- Django、Sphinx、Matplotlib 共 3 个 Guard：3/3 official resolved。
- 预期 marker 0，实际 marker 0；unsafe replay 0。

### Golden-10 Expansion

| 指标 | P2-R0 | Treatment |
| --- | ---: | ---: |
| Planned | 10 | 10 |
| Official resolved | 5 | 4 |
| Official unresolved | 0 | 5 |
| Empty/skipped | 5 | 1 |
| Provider infrastructure cases | 0 | 1 |

已知 resolved 中，`matplotlib-13989` 和 `sympy-20590` 回归；`django-13028` 新增 resolved，不能抵消
non-regression veto。

## 决策

**Case-level 机制证据保留，全局/default Treatment 拒绝并回滚。** 回滚提交：
`042846a`。Gate 2（机制）和 Gate 3（Target transition）通过，但 protocol completeness、Golden-10
non-regression 与 release closure 未通过。

## 最重要的工程结论

这轮展示了一条完整的 failure-driven 链路：从历史 Trace 中识别幂等 Ledger 的状态机缺口，定义
极窄状态转换，设计 activation verifier，再用 Target、Guard 和 Golden expansion 分层验证。最终结果也
说明：强 Case-level 因果故事不能替代集合级 non-regression。

## 声明边界

- Target 是 post-hoc representative Case，不是盲选 population sample。
- Phase 1 与 Phase 2 的 provider、模型和预算不同，不能直接比较总体提升。
- Verifier 机器证明的是 replay 状态转换与 operated path retention，不证明 marker 是 official pass 的
  唯一原因。
- Golden expansion 有一个 provider transport failure；即使忽略它，4/10 < 5/10 和两个已知回归也
  会独立触发 reject。

## 证据定位

- 历史实验总记录：`3a96f14403419a85d1662d501291c75c799237c6:benchmarks/runtime-quality/golden-10-v1.json`
- 文件 SHA-256：`17942805a56dc13d12566864d2ceb81ddb0342b598794ef9bf02c04fbf9918a9`
- 对应字段：`phase2`

```bash
git show 3a96f14:benchmarks/runtime-quality/golden-10-v1.json | jq '.phase2'
```
