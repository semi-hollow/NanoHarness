# 14-how-eval-runner-works

## 1. 这篇解决什么问题
解释为什么 demo 不等于评测，以及如何批量验证 case。

## 2. 先给结论
EvalRunner 应执行真实 verify.py，而不是硬编码 pass。

## 3. 最小概念
case、verify、report、失败原因。

## 4. 对应代码在哪里
`agent_forge/eval/eval_runner.py`、`eval_cases/*/verify.py`。

## 5. 运行一下看效果
`python -m agent_forge.eval.eval_runner`。

## 6. 常见坑
只生成报告不执行 case，结果没有可信度。

## 7. 面试怎么说
我把 failure recovery 也加进 eval（case_006），保证不是 happy path only。

## 8. 下一步学什么
扩展统计项：tool call count、handoff count、safety violation。
