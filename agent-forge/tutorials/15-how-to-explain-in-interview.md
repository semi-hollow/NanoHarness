# 15-how-to-explain-in-interview

## 1. 这篇解决什么问题
把项目从功能列表讲成技术故事。

## 2. 先给结论
先讲问题：如何把 LLM 变成受控执行系统；再讲方案：loop/tools/safety/trace/eval。

## 3. 最小概念
面试官看的是 technical depth、ownership、evidence，不是你堆了多少文件。

## 4. 对应代码在哪里
代码证据分布在 `runtime/`、`tools/`、`safety/`、`observability/`、`eval/`。

## 5. 运行一下看效果
面试前跑 `python3.11 -m unittest discover tests` 和 `python3.11 -m agent_forge.eval.eval_runner`。

## 6. 常见坑
不要说“我做了一个 AI agent”；要说“我实现了 Agent control layer”。

## 7. 面试怎么说
我设计并验证了工具执行、安全边界、观测和评估，而不是只调用模型 API。

## 8. 下一步学什么
读 `16-how-to-tell-project-story.md`。
