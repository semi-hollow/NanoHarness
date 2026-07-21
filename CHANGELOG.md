# 变更记录

## 版本 0.8.0 - 2026-07-18

- 增加分层 Instruction Resolver，按 global、repository、directory、local override、
  runtime override 合并规则，并记录来源、优先级、哈希、字节预算和截断事实。
- 将 Skill 选择拆为 metadata discovery 与正文 activation；模型只接收已激活 Skill，
  trace 保留版本、来源、选择原因和正文规模，不执行任意 Skill 脚本。
- 增加可组合 Lifecycle Hook API，覆盖 model/tool/checkpoint/stop；默认安全 Hook 不会被
  附加 Hook 替换，前置与完成门禁异常 fail closed，后置异常隔离，最终脱敏固定最后执行。
- 增加嵌入式 `RunController` 的协作式 pause、cancel、steer；控制信号只在模型/工具安全
  边界生效，checkpoint 保留 continuation，已执行副作用不自动回滚。
- 增加脱敏有序 `RuntimeEvent` 流和可选 OpenTelemetry 双写 Adapter；model/tool span 由
  真实 started/completed 事件配对，内部 JSON evidence 仍是权威事实源。
- 增加 `ModelCapabilities` 协商：模型 context window 限制输入预算，非并行模型每 turn
  只执行一个工具，内置 OpenAI-compatible transport 可在原生 tools 与受限 JSON 文本
  协议之间切换；其余 capability 字段仅作为 Adapter 声明与 trace evidence。

## 版本 0.7.0 - 2026-07-18

- 增加稳定顶层 `Harness.run/resume`、类型化 `HarnessConfig`、`RunRequest`、`RunResult`
  和只依赖公开 import 的嵌入示例；内部 AgentLoop 与 application service 不再是推荐接入面。
- 将已有 Model、Tool、Context、State、Event、Environment 和 Memory Port 从
  `agent_forge.extensions` 统一导出，并允许 composition root 按端口覆盖默认 Adapter。
- 增加严格版本化的 `forge run --config` YAML/JSON 装配、built-in tool allowlist、
  CLI/模型环境/config/default 优先级，以及不含密钥的 `resolved_config.json`。
- 明确 `0.x` Public API 与 internal module 兼容边界，并用真实 CLI path、resume、配置拒绝、
  外部 consumer 和架构测试覆盖。

- 将超长 `AgentLoop.run` 收敛为 start、prepare、turn 和 stop 阶段编排。
- 增加显式 `AgentRunSession`、`ToolExecutionPipeline` 和 `RunLifecycle`，分别拥有
  per-run 数据、工具治理和 checkpoint/HITL/terminal persistence。
- 修复 `locked`/`dry-run` 策略拒绝写工具时读取越界恢复变量、导致运行崩溃的问题。
- 增加折叠阅读分层、代码导航约束和真实 permission-denial 行为回归。

## 版本 0.6.0 - 2026-07-12

- 用原子 pending/responded/cancelled request、同 turn 副作用 barrier、`waiting_human`
  checkpoint、当时的 `forge respond` 加 resume，替换模拟 `ask_human` 行为；该旧入口现已并入 `forge resume`。
- 增加基于真实隔离 AgentLoop worker 的 validated live fanout，包括 declared/actual
  write-scope gate、确定性 binary patch integration，以及能看见 candidate diff 且带
  pre/post mutation gate 的隔离只读 finalizer；plan 支持低于 global runtime ceiling 的
  per-task step budget。
- 增加 incremental fanout checkpoint、plan/base identity check、patch hash、稳定 worker
  clarification thread、未完成 task selective rerun，以及 current/resumed/evidence-chain
  usage 分层记账。
- 统一 run、benchmark、tool、coordinator 的 candidate diff collection，使 tracked 和
  untracked source file 共用一条 evidence path，同时排除 untracked `.agent_forge` artifact。
- 增加受限 fanout UI/CLI、安全 sample plan、focused regression、带语义断言的真实
  provider smoke，并更新 capability/learning/failure 文档。
- 优化桌面和移动端本地工作台，提供稳定导航、可读表格、受限 control 和 fanout
  artifact view。
- 增加全仓库 function type contract、真实 validated `TraceEvent` envelope、显式
  checkpoint transition、mypy/AST regression gate，以及 object-first 代码阅读地图。

## 版本 0.5.0 - 2026-07-11

- 增加 per-case official SWE-bench result parsing，显式区分 resolved、unresolved、
  error、empty-patch、incomplete。
- 增加固定五仓库 regression set、denominator-aware scorecard 和 matched-run
  `forge eval ablation` report。
- 增加可观测 `task-aware` vs `all` tool-routing experiment，两侧保持相同 runtime
  safety chain。
- 增加隔离 snapshot 上的 OCI-container execution，包括 network/resource limit、
  capability removal、read-only root、command delegation 和 environment manifest
  replay evidence。

## 版本 0.4.0 - 2026-07-11

- 增加 completed run 的 human outcome 和 failure label capture。
- 增加 trace、tool-policy、environment、evaluation 和 feedback evidence 的
  privacy-conscious JSONL export。
- 将 local/detached-worktree execution mode 接入 public run/resume command，保留
  environment manifest 和 patch。
- 将 workbench 和文档主线调整为 runtime/evaluation evidence。
- 增加稳定 runtime capability guide、feedback-loop design note 和显式 roadmap boundary。

## 更早版本

早期版本建立了标准 AgentLoop、context construction、governed tools、human approval、
operation ledger、checkpoint resume、SWE-bench-shaped run、failure taxonomy 和 direct
baseline comparison 和 artifact-based multi-agent coordination。
