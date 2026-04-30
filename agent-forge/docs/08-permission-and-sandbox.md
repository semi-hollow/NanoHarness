# 08-permission-and-sandbox

Agent 最危险的能力不是生成文本，而是执行工具。

## Permission

`PermissionDecision` 有三种：

- allow
- ask
- deny

策略：

- read/list/grep allow；
- write/apply_patch ask；
- dangerous command deny；
- network/delete/external_directory deny。

## Sandbox

`WorkspaceSandbox` 做两件事：

- 所有路径必须在 workspace 内；
- `.env`、`id_rsa`、`.pem`、`.key`、credentials、secrets 拒绝访问。

边界判断使用 `Path.relative_to`，避免 `/tmp/work` 和 `/tmp/work_evil` 的前缀绕过。

## Command Policy

允许 unittest 和 git status/diff；拒绝 rm、curl、wget、ssh、sudo、shutdown、git push 等危险命令。

面试讲法：我把 bash/run_command 当成最高风险工具，因此必须 allowlist、timeout、shell=False、trace。
