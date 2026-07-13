# 安全策略

Agent Forge 是本地 CodingAgent Runtime Core，可以在 workspace 中读写文件、应用 patch
和运行已批准 command，因此即使只用于本地开发，也必须明确 security boundary。

## 当前安全边界

仓库目前提供：

- 通过 `WorkspaceSandbox` 检查 workspace path。
- 通过 `CommandPolicy` 使用 command allowlist，并阻断高风险 command。
- 通过 runtime hook 提供 approval mode。
- Multi-agent coordinator 中的 role-level tool allowlist。
- Multi-agent 通过 artifact handoff 通信，而不是隐藏 peer chat。
- 可选 worktree execution，将代码修改与主 checkout 隔离。
- 可选 OCI execution，在 detached snapshot 上隔离 command/diagnostics process，并限制
  network、CPU、memory、PID、capability 和 read-only root。
- `network-policy=deny` 时，environment hook 阻断已知 network executable；所有 mode
  下 command allowlist 也独立排除 network tool。
- MCP web tool 默认 offline。

Local 和 worktree mode 不是 OS sandbox。OCI mode 使用 Docker-compatible runtime，
但不等价于 Firecracker、gVisor 或 managed remote execution service，也不是 hardened
hostile multi-tenant boundary。File tool 仍在 host Agent Forge process 中运行，
`WorkspaceSandbox` 会将其限制在挂载到 container 的 isolated snapshot。

`network-policy=allow` 只给 OCI container bridge network，不会扩大 `RunCommandTool`
executable allowlist。Project test 只能通过 policy 已允许的 command 使用 container network。

## 报告漏洞

对于公开仓库，优先创建 private security advisory。如果仓库没有启用 private advisory，
请先直接联系 owner，再公开 exploit detail。

报告应包含：

- 受影响 file/module。
- 可复现步骤。
- 预期行为与实际行为。
- 问题是否可以读取 secret、执行非预期 command、逃逸 workspace 或绕过 approval。

## Secret 处理

不要提交真实 provider key。使用 shell environment variable 或被忽略的本地文件：

- `DEEPSEEK_API_KEY`
- `OPENAI_API_KEY`
- `AGENT_FORGE_API_KEY`

仓库默认忽略 `.env`、`.env.local`、`llm_profiles.json` 和 `.agent_forge/`。

当前版本没有实现 Claude / Anthropic provider compatibility。未来只有在对应 provider
module 被明确实现并 review 后，才应增加 Anthropic credential。

## Disclosure 范围

外部 model provider、operating system、shell 或 package manager 的安全问题不属于本
仓库，除非 Agent Forge 错误处理其 response 或绕过了自身 policy。
