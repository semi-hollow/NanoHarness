# Quality Selection v1 失败关闭实验

## 实验身份

- 日期：2026-08-12
- 问题：在固定 Golden-10 与质量优先配置下，`deepseek-v4-pro` 和 `glm-5.2` 哪一个适合作为后续
  Canonical measurement 的固定模型？
- Provider：`opencode-go`。
- 计划：10 个 Case × 2 个候选，共 20 个单次槽位；串行交错 schedule；不允许 correctness rerun。
- Source tag：`canonical-showcase-quality-selection-preflight-20260812`。

## 发生了什么

- 20/20 槽位都留下 finalized root artifacts。
- 9/20 槽位含 shared provider rate-limit 记录。
- 第 12—14 槽已经出现限流；最后 6 槽两个候选都在首个模型调用处耗尽两次 transport attempts。
- 只有 11 个槽位完全没有 rate-limit 记录，但它们不是预注册的完整比较分母。
- 成功响应上的 requested/provider-reported model 一致，fallback=0；问题是共享配额污染，不是模型
  身份切换。

## 决策

**`invalid_no_winner`。** Summarizer exit 2，winner 和 selected model 都保持 null。没有 correctness
重跑、official evaluator 重跑或整题重跑，也没有从前 14 槽、11 个无 rate-limit 槽或任一 shard
子集挑选赢家。

## 最重要的工程结论

模型选型除了固定模型、Case 和预算，还需要在 denominator 启动前验证 provider capacity，并把
readiness、pacing、observed model、transport attempt 和完整分母作为发布前置条件。基础设施污染后，
“仍有部分可看结果”不是继续比较的理由。

## 声明边界

- 该记录只证明选型流程正确失败关闭，不证明任何候选质量高低。
- 20 个 finalized roots 不等于 20 个有效 correctness observations。
- 任何 partial prefix 都不能倒推出 winner。
- 事故没有修改 Canonical headline 或当前 selected model。

## 证据定位

- [机器记录](../../archive/quality-selection-v1-fail-closed.json)
- 当前文件 SHA-256：`36a34f08a1941a86007fa4d2d7878fa583ca7794e895b9460ab8cec48405b834`
- [冻结协议](../../showcase/quality-selection-protocol-v1.json)
- [冻结命令清单](../../showcase/quality-selection-command-manifest-v1.json)
- 历史归档提交：`c7594bf54b4f8df536a430131623514c03ada5f2`
