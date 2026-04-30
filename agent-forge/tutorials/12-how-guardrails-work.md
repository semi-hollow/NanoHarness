# 12-how-guardrails-work

## 1. 这篇解决什么问题
解释 guardrails 和 permission 的区别。

## 2. 先给结论
Permission 控制动作能不能执行；Guardrail 检查输入、工具调用和最终输出是否安全可信。

## 3. 最小概念
本项目有 input/tool/output 三类 guardrail，每个结果有 category、reason、severity。

## 4. 对应代码在哪里
`agent_forge/safety/guardrails.py`。

## 5. 运行一下看效果
`python3.11 -m unittest tests.test_guardrails`。

## 6. 常见坑
输出也要 guardrail：不能没跑测试却说“测试通过”。

## 7. 面试怎么说
我用多层防线处理 Agent 风险，而不是只写一句 system prompt。

## 8. 下一步学什么
读 `13-how-tracing-works.md`。
