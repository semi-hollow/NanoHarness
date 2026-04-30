# 00-how-to-learn-this-project

## 1. 这篇解决什么问题
帮你按最短路径读懂 Agent Forge，不被目录数量吓到。

## 2. 先给结论
先跑 demo，再看 loop，再看 tools/safety/trace/eval，最后读面试材料。

## 3. 最小概念
Agent Harness 是 LLM 外面的控制层：它负责上下文、工具、权限、观察、停止和评估。

## 4. 对应代码在哪里
入口是 `run_demo.py` 和 `agent_forge/cli.py`；核心是 `runtime/agent_loop.py`。

## 5. 运行一下看效果
`python3.11 run_demo.py --mode single`，看它读文件、patch、跑测试、写 trace。

## 6. 常见坑
不要先读所有 docs；先看 trace 里的事件顺序，反推代码结构。

## 7. 面试怎么说
我用这个项目学习的不是“怎么调 API”，而是 Agent 的控制层怎么设计。

## 8. 下一步学什么
读 `01-why-mock-llm.md`，理解为什么默认不用真实模型。
