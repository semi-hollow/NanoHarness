# 贡献指南

NanoHarness 优先保证局部可读性：读者应该仅通过函数名、签名、附近 domain type 和
一层调用，就能理解一个函数。

仓库应继续聚焦 runtime control plane。Contribution 不应把它扩张成 IDE 产品、
hosted service 或 model-training stack。

## 本地环境

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip setuptools wheel
python -m pip install -e '.[bench,dev]'
```

## 编码规则

1. 每个 production function 都必须有完整参数和返回类型。
2. Runtime 自有状态优先使用具名 dataclass 或 Enum。
3. `Any` 只保留在不可信 JSON、HTTP、MCP、UI 或 provider boundary。
4. Boundary data 进入自有 runtime state 前必须校验。
5. 不使用 `**kwargs`、`setattr` 或 string key 隐藏核心状态迁移。
6. 不复用同一个变量名表示不同类型或含义。
7. 在 storage boundary 使用 `to_dict` 序列化，不要在远端 caller 提前序列化。
8. 高价值 trace event 应有带类型签名的具名 `record_*` 方法。
9. Side effect 必须在拥有它的函数中可见。
10. Behavior change 必须增加 regression test，并更新 failure-driven improvement log。
11. 每项用户可见能力的 orchestration method 标记 `PRIMARY ENTRYPOINT`。
12. 跨模块调用的 public persistence/policy/evidence boundary 标记 `RUNTIME PORT`。
13. Entrypoint docstring 必须写明 caller、下一 owner，以及 evidence 或 return value。

不要给所有 public method 都打标记。Constructor、data accessor、renderer 和 storage
helper 默认不标记，除非它们是真实跨模块边界。Multi-actor state machine 可以有多个
primary entry，但必须在 `docs/ARCHITECTURE.md` 或对应 ADR 中说明它们之间的 transition。

## 验证

```bash
python -m pip install -e '.[dev]'
scripts/verify.sh
```

验证流程会 compile package、对 `agent_forge` 运行 mypy，并执行 behavior regression
suite。修改 runtime contract 前先阅读 `docs/ARCHITECTURE.md` 和对应 Capability API。

MCP behavior 改变时同时运行 `scripts/verify_mcp.sh`。配置 `DEEPSEEK_API_KEY` 后，
real-model smoke 会自动执行。

## 文档语言

- 项目介绍、架构契约、能力边界、环境搭建和评测证据文档统一使用中文。
- 类名、方法名、CLI、状态值、artifact 字段和行业术语保留源码中的英文，确保可搜索。
- 第三方仓库原文、真实 benchmark 输出和历史运行 artifact 保持原样，不改写证据。
- 个人学习路径、代码阅读笔记和个人准备材料不属于本仓库范围。

## 文档治理

公开文档按问题分工，同一事实只在一个 owner 中完整说明：

| 文档 | 唯一职责 |
| --- | --- |
| `README.md` | 项目展示、证据语义、五分钟上手和阅读入口 |
| `docs/ARCHITECTURE.md` | 稳定分层、依赖方向、运行链路和公共契约 |
| `docs/CAPABILITY_REALITY_MATRIX.md` | 能力成熟度、真实边界和禁止 claim |
| `docs/PROJECT_EVOLUTION.md` | 已经发生且可由代码、测试或提交验证的演进 |
| `docs/ROADMAP.md` | 尚未完成的工作、优先级和完成定义 |
| `docs/evaluation/failure-driven-improvements.md` | 可检索的真实失败、根因、修复和回归证据 |

其余文档只能归入 `docs/adr`、`docs/architecture`、`docs/case-studies` 或
`docs/evaluation`，并回答一个稳定且独立的问题。新增前先更新既有 owner；确实无法归入时，
新文档必须从 README 或 owner 文档获得入口，并说明它替代了什么重复内容。

- 不在公开仓库新增学习路线、面试问答、个人复盘或阶段实施计划。
- 生成的 trace、report 和 benchmark artifact 留在 `.agent_forge`，不复制进 `docs`。
- 运行时事实变化时更新 owner；其他文档只引用，不复制完整说明。
- 历史计划和过时方案由 Git 历史保留，不维持第二套“历史文档树”。
- README、演进史和路线图有体量门禁；失败案例日志允许随真实证据持续增长。
- `docs/evaluation/failure-driven-improvements.md` 是受保护的一手记录；文档清理不得删除案例、
  截断证据链，或用“可从 Git 恢复”替代当前树中的可检索档案。

## 仓库卫生

- 变更尽量收敛在一个 owning layer。
- 不提交 API key、`.env`、生成的 `.agent_forge` artifact 或个人 IDE state。
- 优先使用 public benchmark task 和 trace evidence，不编造 success claim。
- Public behavior 改变时同步更新 architecture 或 evaluation 文档。

## 提交历史

- 标题描述一个可观察行为，推荐 `feat(runtime): persist waiting-human checkpoint` 形式。
- 修复应说明被纠正的语义，例如 `fix(evaluation): write case study after final diagnosis`。
- 架构调整使用 `refactor(<capability>)`，并在正文记录迁移边界和行为不变量。
- 避免 `complete`、`finalize`、`production-ready`、`readiness`、`polish` 等无法验证的表述。
- 不在标题中堆叠多条 bullet；正文写清为什么改、改变什么、不改变什么、如何验证。
- 能力分阶段成熟时，正文标明 `Introduces`、`Hardens` 或 `Replaces`。

已公开的 `master` 不为美化标题而重写历史。历史提交的权威语义索引见
[工程演进史](docs/PROJECT_EVOLUTION.md)。

## Pull Request 检查表

- [ ] `scripts/verify.sh` 通过。
- [ ] MCP behavior 改变时，`scripts/verify_mcp.sh` 通过。
- [ ] Mypy 和 type-contract regression test 通过。
- [ ] 项目公开文档通过中文优先检查。
- [ ] Method body 折叠后，capability entrypoint 和 runtime port 仍清晰可见。
- [ ] README 或 docs 已反映用户可见行为。
- [ ] 没有 tracked secret、personal path 或 generated run artifact。
- [ ] 新 runtime behavior 有 trace/evaluation evidence 和 failure-log case。
