# 12-production-readiness

Agent Forge V2 不是线上平台，但它把生产化要讨论的控制面拆开了：model gateway、工具权限、沙箱、trace、eval、回滚和事故处理。

## 运行形态

| 形态 | 目标 | 风险 | 建议 |
| --- | --- | --- | --- |
| local developer | 本地学习、debug、面试演示 | 误读/误写本地文件 | 默认 MockLLM，workspace sandbox，写入 approval |
| CI runner | PR 中自动跑 tests/eval | 命令越权、网络外连、密钥泄露 | 命令 allowlist、禁 curl/wget/ssh、只挂载必要目录 |
| internal server | 团队内网页/API 调用 Agent | 多租户隔离、审计、成本 | 请求鉴权、租户级 rate limit、trace 留存 |
| GitHub PR bot | 自动读 diff、评论、提 patch | 错改代码、误发评论、权限扩大 | 最小 GitHub token、draft PR、人工 merge gate |

## Model Gateway

V2 增加了可选 `OpenAICompatibleLLMClient`，但默认仍是 `MockLLMClient`。生产化时应通过 model gateway 管模型调用，而不是让业务代码散落多个 provider SDK。

Gateway 应负责：

- auth：统一 API key、租户身份、服务账号。
- rate limit：按用户、仓库、任务类型限流。
- routing：简单任务走便宜模型，复杂任务走强模型。
- fallback：主 provider 超时或 invalid response 时切 mock、降级模型或返回可恢复错误。
- audit：记录 model、prompt hash、tool schema、response summary，不直接泄露密钥。
- cost：记录 token/请求数，给 eval 和项目维度做预算。

## Permission 与 Sandbox

Agent Forge 的风险集中在工具执行。V2 保留三层：

- input/output guardrail：拦截危险任务和未验证的最终声明。
- permission policy：把 read/write/run_command 分开，写入可要求 human approval。
- workspace sandbox：路径必须在 workspace 内，`.env`、key、pem、credentials、secrets 默认拒绝。

CI 和 PR bot 中应进一步收紧：

- 禁止网络命令，除非 runner 本身有受控代理。
- 禁止 shell=True，命令走 argv。
- 工具执行账户使用最小权限。
- 每次写入都关联 trace run_id。

## Audit、Rollback、Incident

生产事故处理要能回答四个问题：

1. Agent 看到了什么 context？
2. 它调用了哪些 tool？
3. 哪一步失败或被 guardrail block？
4. 如何撤回或限制下一次同类行为？

Agent Forge 的证据链：

- `agent_forge_trace.json`：事件级 trace。
- metrics summary：tool call、failed tool call、handoff、guardrail、approval、duration。
- eval report：case 级通过率和失败列表。
- git diff/status tool：变更可审查。

回滚策略：

- local developer：直接看 git diff，人工 revert。
- CI runner：不持久化写入，失败即丢弃 workspace。
- internal server：每次任务生成 patch，不自动 merge。
- GitHub PR bot：只开 draft PR，保护分支由 reviewer merge。

## Rollout

建议 rollout 顺序：

1. MockLLM + eval，只验证 agent loop 和工具边界。
2. shadow mode 接真实 LLM：只记录建议，不执行写入。
3. 低风险 read-only 工具开放。
4. 小范围写入工具 + approval。
5. 按仓库/团队扩大，并把失败 case 反哺 eval。
