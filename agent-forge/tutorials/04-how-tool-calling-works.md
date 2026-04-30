# 04-how-tool-calling-works

## 1. 这篇解决什么问题
解释模型返回工具调用后，系统如何安全执行。

## 2. 先给结论
LLM 只提出 tool call；真正是否执行由 ToolRegistry、参数校验、permission 和 sandbox 决定。

## 3. 最小概念
Tool schema 是模型和 runtime 的契约；Observation 是工具执行后的统一反馈。

## 4. 对应代码在哪里
`agent_forge/tools/registry.py`、`tools/base.py`、各个 `tools/*.py`。

## 5. 运行一下看效果
`python3.11 -m unittest tests.test_tools`。

## 6. 常见坑
unknown tool 和 invalid arguments 不能让程序崩溃，应该返回失败 Observation。

## 7. 面试怎么说
我没有信任模型输出，而是在 runtime 层做 schema 和权限兜底。

## 8. 下一步学什么
读 `05-how-observation-works.md`。
