from dataclasses import dataclass


# 核心数据：一次工具可见性决策的任务、候选 schema 与运行上下文。
@dataclass(frozen=True)
class ToolRoutingRequest:
    """Router 的完整输入；schema 不在 Router 内修改。"""

    task: str
    schemas: list[dict]
    step: int = 1
    agent_name: str = ""
    skill_tool_names: set[str] | None = None
    mode: str = "task-aware"


# 核心数据：本 turn 展示给模型和隐藏于模型的真实工具可见性决策。
@dataclass(frozen=True)
class ToolRoute:
    """一次工具可见性决策，明确记录展示、隐藏和治理元数据。"""

    schemas: list[dict]
    allowed_names: set[str]
    reason: str
    dropped_names: list[str]
    metadata: dict[str, dict]

    def policy_summary(self) -> dict[str, object]:
        """生成可写入 trace 和 UI 的真实路由摘要。"""

        return {
            "allowed_tools": sorted(self.allowed_names),
            "hidden_tools": list(self.dropped_names),
            "tool_count": {
                "allowed": len(self.allowed_names),
                "hidden": len(self.dropped_names),
            },
            "metadata": self.metadata,
        }


class ToolRouter:
    """根据任务、Skill 和运行模式收敛模型可见的工具集合。"""

    DEFAULT_METADATA = {
        "list_files": {
            "capability": "discover",
            "risk": "low",
            "latency": "low",
            "mode": "read",
        },
        "read_file": {
            "capability": "inspect",
            "risk": "low",
            "latency": "low",
            "mode": "read",
        },
        "grep": {
            "capability": "search",
            "risk": "low",
            "latency": "low",
            "mode": "read",
        },
        "grep_search": {
            "capability": "search",
            "risk": "low",
            "latency": "low",
            "mode": "read",
        },
        "diagnostics": {
            "capability": "validate",
            "risk": "low",
            "latency": "medium",
            "mode": "read",
        },
        "git_status": {
            "capability": "diff",
            "risk": "low",
            "latency": "low",
            "mode": "read",
        },
        "git_diff": {
            "capability": "diff",
            "risk": "low",
            "latency": "low",
            "mode": "read",
        },
        "ask_human": {
            "capability": "clarify",
            "risk": "low",
            "latency": "human",
            "mode": "human",
        },
        "apply_patch": {
            "capability": "edit",
            "risk": "medium",
            "latency": "low",
            "mode": "write",
        },
        "write_file": {
            "capability": "edit",
            "risk": "high",
            "latency": "low",
            "mode": "write",
        },
        "run_command": {
            "capability": "validate",
            "risk": "high",
            "latency": "medium",
            "mode": "command",
        },
    }

    # 主要入口：结合任务、Skill 与模式收敛本 turn 的模型可见工具 schema。
    def route(self, request: ToolRoutingRequest) -> ToolRoute:
        """返回本轮允许展示给模型的 schema，并保留隐藏原因证据。"""

        task = request.task
        schemas = request.schemas
        step = request.step
        agent_name = request.agent_name
        skill_tool_names = request.skill_tool_names
        mode = request.mode
        lowered = (task or "").lower()
        by_name = {schema.get("name", ""): schema for schema in schemas}
        names = set(by_name)
        if mode not in {"task-aware", "all"}:
            raise ValueError(f"unsupported tool routing mode: {mode}")
        if mode == "all":
            return ToolRoute(
                schemas=list(schemas),
                allowed_names=set(names),
                reason=(
                    f"mode=all selected={len(schemas)} dropped=0 step={step} "
                    f"agent={agent_name or 'agent'}"
                ),
                dropped_names=[],
                metadata={
                    name: self.DEFAULT_METADATA.get(
                        name,
                        {
                            "capability": "external",
                            "risk": "configured",
                            "latency": "unknown",
                            "mode": "mcp_style",
                        },
                    )
                    for name in sorted(names)
                },
            )
        read_only_markers = [
            "不要修改",
            "不修改",
            "不要改",
            "不改",
            "只读",
            "仅阅读",
            "do not modify",
            "do not edit",
            "read only",
            "without editing",
        ]
        read_only_requested = any(marker in lowered for marker in read_only_markers)
        if read_only_requested and any(
            marker in lowered
            for marker in [
                "do not edit tests unless",
                "do not modify tests unless",
                "不要修改测试，除非",
                "不要改测试，除非",
            ]
        ):
            read_only_requested = False

        selected = {
            name
            for name in names
            if self.DEFAULT_METADATA.get(name, {}).get("capability")
            in {"discover", "inspect", "search"}
        }

        selected |= names & {"ask_human"}

        if not read_only_requested and any(
            token in lowered
            for token in [
                "fix",
                "repair",
                "resolve",
                "patch",
                "implement",
                "修复",
                "实现",
                "补充",
            ]
        ):
            selected |= names & {
                "apply_patch",
                "write_file",
                "run_command",
                "diagnostics",
                "git_status",
                "git_diff",
            }
        if not read_only_requested and any(
            token in lowered
            for token in ["test", "validate", "验证", "测试", "unittest"]
        ):
            selected |= names & {"run_command", "diagnostics"}
        if any(token in lowered for token in ["review", "diff", "审查", "回滚"]):
            selected |= names & {"git_diff", "git_status", "read_file"}
        if any(
            token in lowered
            for token in ["clarify", "unclear", "ambiguous", "澄清", "不明确"]
        ):
            selected |= names & {"ask_human"}

        if skill_tool_names:
            selected |= names & skill_tool_names
        if read_only_requested:
            selected -= {"apply_patch", "write_file", "run_command"}

        if "swe-bench" in lowered or "swebench" in lowered:
            selected -= {"run_command", "write_file"}
            selected |= names & {"apply_patch", "diagnostics", "git_diff", "git_status"}

        external_names = names - set(self.DEFAULT_METADATA)
        task_terms = {
            term
            for term in lowered.replace("_", " ").replace(".", " ").split()
            if len(term) >= 3
        }
        for name in external_names:
            schema = by_name[name]
            searchable = f"{name} {schema.get('description', '')}".lower()
            if (
                "mcp" in lowered
                or "tool" in lowered
                or "policy" in lowered
                or any(term in searchable for term in task_terms)
            ):
                selected.add(name)

        if not selected:
            selected = set(names)

        routed = [schema for schema in schemas if schema.get("name") in selected]
        dropped = sorted(names - selected)
        reason = (
            f"selected={len(routed)} dropped={len(dropped)} step={step} "
            f"agent={agent_name or 'agent'} skill_tools={len(skill_tool_names or set())}"
        )
        return ToolRoute(
            schemas=routed,
            allowed_names={schema.get("name", "") for schema in routed},
            reason=reason,
            dropped_names=dropped,
            metadata={
                name: self.DEFAULT_METADATA.get(
                    name,
                    {
                        "capability": "external",
                        "risk": "configured",
                        "latency": "unknown",
                        "mode": "mcp_style",
                    },
                )
                for name in sorted(selected)
            },
        )
