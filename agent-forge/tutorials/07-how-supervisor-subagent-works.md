# 07-how-supervisor-subagent-works

## 1. 这篇解决什么问题
解释 Supervisor 和 Subagent 如何协作。

## 2. 先给结论
Supervisor 负责状态机和 handoff，Planner/Coding/Tester/Reviewer 只做各自职责。

## 3. 最小概念
Subagent 不是普通工具；它代表一个角色，有输入状态和输出结果。

## 4. 对应代码在哪里
`agent_forge/agents/supervisor_agent.py`、`planner_agent.py`、`coding_agent.py`、`tester_agent.py`、`reviewer_agent.py`。

## 5. 运行一下看效果
`python3.11 run_demo.py --mode multi`，看输出中的 retry 和 review。

## 6. 常见坑
多 Agent 容易变乱，所以本项目用 `TaskPhase` 让 Supervisor 统一编排。

## 7. 面试怎么说
我没有让 subagent 互相乱调，而是用 Supervisor 控制阶段和责任边界。

## 8. 下一步学什么
读 `08-how-handoff-works.md`。
