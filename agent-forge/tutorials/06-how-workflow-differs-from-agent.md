# 06-how-workflow-differs-from-agent

## 1. 这篇解决什么问题
解释 workflow 和 agent mode 的区别。

## 2. 先给结论
Workflow 是固定路径，Agent 是动态决策；生产系统常常混用。

## 3. 最小概念
固定任务适合 workflow，开放问题适合 agent loop。

## 4. 对应代码在哪里
`agent_forge/workflows/coding_workflow.py` 和 `agent_forge/runtime/agent_loop.py`。

## 5. 运行一下看效果
`python3.11 run_demo.py --mode workflow` 对比 `--mode single`。

## 6. 常见坑
不要为了“多 agent”把简单流程复杂化；稳定路径应优先 workflow。

## 7. 面试怎么说
我保留两种模式，是为了说明确定性和灵活性的 trade-off。

## 8. 下一步学什么
读 `07-how-supervisor-subagent-works.md`。
