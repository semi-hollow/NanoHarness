# Settlement Reconciliation Fixture

这是 NanoHarness 复杂真实模型 Lab 使用的隔离练习仓库。

系统接收支付渠道的 capture 回调，并维护结算账户、幂等键和账本。初始实现故意保留一组相互关联的
缺陷：渠道重试可能重复入账、金额舍入不符合业务规则、失败事件可能提前污染幂等状态或账本。

运行时任务会要求 Agent 先执行 focused test，再完成全量回归。测试是验收契约，不允许修改。
