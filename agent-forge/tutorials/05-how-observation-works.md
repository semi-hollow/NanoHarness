# 05-how-observation-works

## 1. 这篇解决什么问题
解释为什么工具结果要统一成 Observation。

## 2. 先给结论
Observation 是 Agent 的反馈信号，决定下一轮是否重试、换工具或结束。

## 3. 最小概念
本项目的 Observation 包含 `tool_name`、`success`、`content`。

## 4. 对应代码在哪里
`agent_forge/runtime/observation.py` 和 `agent_loop.py` 中的 `tool_observation` 事件。

## 5. 运行一下看效果
single demo 第一次 patch 会得到失败 Observation：`old text not found`。

## 6. 常见坑
只打印 stdout 不够；Agent 需要结构化知道成功还是失败。

## 7. 面试怎么说
Observation 让工具执行从黑盒变成可反馈、可追踪的闭环。

## 8. 下一步学什么
读 `06-how-workflow-differs-from-agent.md`。
