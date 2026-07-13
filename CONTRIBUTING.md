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
primary entry，但必须在 `docs/guides/code-reading-map.md` 中说明它们之间的 transition。

## 验证

```bash
python -m pip install -e '.[dev]'
scripts/verify.sh
```

验证流程会 compile package、对 `agent_forge` 运行 mypy，并执行 behavior regression
suite。修改 runtime contract 前先阅读 `docs/guides/code-reading-map.md`。

MCP behavior 改变时同时运行 `scripts/verify_mcp.sh`。配置 `DEEPSEEK_API_KEY` 后，
real-model smoke 会自动执行。

## 文档语言

- 项目介绍、架构说明、代码导览、学习路径、环境搭建和评测教学文档统一使用中文。
- 类名、方法名、CLI、状态值、artifact 字段和行业术语保留源码中的英文，确保可搜索。
- 第三方仓库原文、真实 benchmark 输出和历史运行 artifact 保持原样，不改写证据。
- 新增教学文档时，将它加入 `tests/test_documentation_language.py` 的中文优先清单。

## 仓库卫生

- 变更尽量收敛在一个 owning layer。
- 不提交 API key、`.env`、生成的 `.agent_forge` artifact 或个人 IDE state。
- 优先使用 public benchmark task 和 trace evidence，不编造 success claim。
- Public behavior 改变时同步更新 architecture 或 evaluation 文档。

## Pull Request 检查表

- [ ] `scripts/verify.sh` 通过。
- [ ] MCP behavior 改变时，`scripts/verify_mcp.sh` 通过。
- [ ] Mypy 和 type-contract regression test 通过。
- [ ] 项目自有教学文档通过中文优先检查。
- [ ] Method body 折叠后，capability entrypoint 和 runtime port 仍清晰可见。
- [ ] README 或 docs 已反映用户可见行为。
- [ ] 没有 tracked secret、personal path 或 generated run artifact。
- [ ] 新 runtime behavior 有 trace/evaluation evidence 和 failure-log case。
