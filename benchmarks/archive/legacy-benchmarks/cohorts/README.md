# Frozen Benchmark Cohorts

当前展示评测的预注册分母已统一迁移到
[Canonical Showcase](../showcase/canonical-50-v1.json)。该清单从固定的 SWE-bench
Verified revision 中离线生成，选择阶段只读取 `instance_id` 与 `repo`，并在任何正式
运行前冻结样本、仓库配额、顺序与 SHA-256。

早期 100 题清单属于低预算实验历史，已从主动展示面移除；仍可从 Git 历史恢复，恢复点见
[评测历史归档](../archive/README.md)。历史 Case 的仅 ID 集合已嵌入 Canonical 排除清单，
因此全新 clone 仍能机械验证当前 50 题没有复用旧开发样本。

Canonical 单次结果只能写成“在预注册固定 50 题样本上 resolved `X/50`”，不得冒充
SWE-bench 官方排行榜或总体解决率。
