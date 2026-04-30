# 14-how-eval-runner-works

## 1. 这篇解决什么问题
解释 demo 和 eval 的区别。

## 2. 先给结论
Demo 用来展示，eval 用来回归；Agent Forge 的 eval 会真实执行每个 `verify.py`。

## 3. 最小概念
每个 case 有 `task.md` 和 `verify.py`；runner 生成 pass rate、failed list 和 trace metrics。

## 4. 对应代码在哪里
`agent_forge/eval/eval_runner.py`、`eval_cases/`。

## 5. 运行一下看效果
`python3.11 -m agent_forge.eval.eval_runner`，再看 `eval_report.md`。

## 6. 常见坑
不要硬编码全通过；指标必须来自 verify 结果和 trace。

## 7. 面试怎么说
我用 eval 覆盖 happy path 和 safety/recovery path，避免只演示成功案例。

## 8. 下一步学什么
读 `15-how-to-explain-in-interview.md`。
