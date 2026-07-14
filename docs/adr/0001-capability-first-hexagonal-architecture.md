# ADR-0001：采用按能力划分的六边形模块化单体

- 状态：Accepted
- 日期：2026-07-14

## 背景

NanoHarness 已按 runtime、multi-agent、evaluation、bench、observability 等能力拆包，
但部分能力内部仍混合领域数据、流程编排、文件存储、Git/进程调用和报告渲染。
随着 HITL、恢复、fanout 和评测证据增加，依赖方向与状态所有权开始变得不清晰。

## 决策

项目保持按能力划分的模块化单体。Runtime、Orchestration 和 Evaluation 等复杂能力
内部使用 Domain、Application、Ports、Adapters 分层，并由显式 composition root
装配。CLI、UI 和 benchmark 作为 inbound adapters，Evidence 使用独立 read model。

迁移采用行为保持的纵向切片：先建立契约和 Runtime 参考实现，再迁移
Orchestration、Evaluation/Evidence，最后收敛 Presentation。历史导入路径在迁移期
迁移调用方后删除旧导入 facade，不让同一能力长期存在两套入口。

## 结果

正面结果：

- 核心流程可以在不启动真实文件、Git 或模型环境的情况下测试。
- 依赖方向可以自动检查。
- 代码阅读顺序稳定为 API、Use Case、Domain、Ports、Adapters。
- 状态与报告结论拥有明确 owner。

代价：

- 需要少量 Protocol、Request/Result 和 wiring 代码。
- 迁移期间需要一次性同步调整调用方和测试，不保留长期兼容路径。
- 过度分层仍可能增加理解成本，因此只治理复杂能力。

## 被否决方案

### 全局技术分层

把整个项目拆成顶层 `domain/`、`application/`、`infrastructure/` 会让同一能力散落在
多个远距离目录，不利于项目阅读和独立演进。

### 大爆炸式目录重写

一次移动所有模块会把架构变化和行为变化混在一起，降低回归定位能力，也会破坏现有
公开导入路径。

### 保持现状，仅增加架构图

文档无法阻止新的反向依赖和职责混合，不能解决 architectural erosion。
