# Interview Q&A (V1)

### Q1：这个项目是什么？
**面试官想看什么：** 你是否理解项目边界。  
**推荐回答：** 这是一个 Agent Harness MVP，不是模型训练项目，重点在可控执行链路：loop、tool、safety、trace、eval。  
**本项目里的证据：** `runtime/agent_loop.py`、`tools/*`、`safety/*`、`eval/eval_runner.py`。  
**可能追问：** 为什么不用现成框架？

### Q2：多 Agent 是真的协作吗？
**面试官想看什么：** 是否有真实分工而非打印字符串。  
**推荐回答：** Supervisor 按阶段 handoff，Planner 产出计划，Coding 真调用 read+patch，Tester 跑 unittest，Reviewer 看 diff+test 结果给结论。  
**本项目里的证据：** `agents/supervisor_agent.py`、`agents/*_agent.py`。  
**可能追问：** 失败时如何回退？

### Q3：为什么强调安全？
**面试官想看什么：** 你是否理解 production 风险。  
**推荐回答：** Agent 最危险在工具执行，所以加 sandbox 路径边界、敏感文件拒绝、命令 allowlist/denylist、write 需 approval。  
**本项目里的证据：** `safety/sandbox.py`、`safety/permission.py`、`tools/run_command.py`。  
**可能追问：** shell=True 为什么不行？

### Q4：怎么证明不是 demo 幻觉？
**面试官想看什么：** 有无可验证证据。  
**推荐回答：** 我保留 trace、跑 unittest、跑 eval cases，case 通过 verify.py 实际执行而非硬编码。  
**本项目里的证据：** `observability/trace.py`、`tests/`、`eval_cases/*/verify.py`。  
**可能追问：** 指标怎么扩展？

> 其余问题按同样模板扩展，V1 已覆盖核心高频追问；下一版可补齐 50 条完整库。
