# 13-how-tracing-works

## 1. 这篇解决什么问题
解释为什么 Agent 必须可观测。

## 2. 先给结论
Trace 记录 Agent 看了什么、计划了什么、调用了什么工具、结果如何、为什么停止。

## 3. 最小概念
Trace event 包含 run_id、step、agent、event_type、duration、success、error 和扩展字段。

## 4. 对应代码在哪里
`agent_forge/observability/trace.py`、`metrics.py`、`summary.py`。

## 5. 运行一下看效果
运行 demo 后看 `agent_forge_trace.json` 和 `summary.md`。

## 6. 常见坑
普通 print 不能支撑 eval 和事故复盘；trace 要结构化。

## 7. 面试怎么说
我用 trace 把 hallucination、tool failure、permission issue 都变成可定位事件。

## 8. 下一步学什么
读 `14-how-eval-runner-works.md`。
