# 技术答辩包

这个目录只服务一个目标：把 Agent Forge 讲成一个可信、可追问、能展开细节的
Senior CodingAgent runtime 项目。它不用于学习源码细节；源码阅读从
`../../agent_forge/README.md` 开始，runtime 设计学习从 `../study-pack/README.md`
开始。

## 文件职责

| 文件 | 作用 | 什么时候读 |
|---|---|---|
| `01-project-briefing.md` | 项目总讲法：30 秒、1 分钟、5 分钟、10 分钟版本。 | 准备开场介绍项目。 |
| `02-question-coverage.md` | 题型覆盖地图：哪些项目里有，哪些只是边界知识。 | 判断问题该用项目回答还是用扩展知识回答。 |
| `03-project-profile.md` | 简历/开源描述：如何写项目、如何不夸大。 | 写简历、GitHub 介绍、项目经历。 |
| `04-core-question-bank.md` | 核心中文问答库：按 Agent 架构、ReAct、工具、记忆、安全、eval 等分类。 | 直接练技术追问。 |
| `05-demo-evidence-walkthrough.md` | 演示和证据讲法：怎么跑、看哪些 trace/usage、如何解释数字。 | 准备现场演示或自我复盘。 |

## 使用顺序

1. 先读 `01-project-briefing.md`，把项目讲成一条主线。
2. 再读 `05-demo-evidence-walkthrough.md`，确保每一句话都有运行证据。
3. 然后读 `04-core-question-bank.md`，按主题练追问。
4. 最后用 `02-question-coverage.md` 检查哪些题不能硬往项目里塞。
5. 需要写简历或项目介绍时再读 `03-project-profile.md`。

核心原则：能用项目回答的，要说代码和证据；项目没有实现的，要说清楚分层边界和扩展点，不编造线上经验。
