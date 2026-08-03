from dataclasses import dataclass


# 核心数据：一次工具可见性决策的任务、候选 schema 与运行上下文。
@dataclass(frozen=True, kw_only=True)
class ToolRoutingRequest:
    """Router 的完整输入；schema 不在 Router 内修改。"""

    task: str
    schemas: list[dict]
    step: int = 1
    agent_name: str = ""
    skill_tool_names: set[str] | None = None
    mode: str = "task-aware"


# 核心数据：本 turn 展示给模型和隐藏于模型的真实工具可见性决策。
@dataclass(frozen=True, kw_only=True)
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
    """把已注册工具投影成本轮允许模型看见的工具集合。

    可类比 API Gateway 的请求级路由表：``ToolRegistry`` 已经回答“系统有什么工具”，
    本类只回答“这个 task 的当前 turn 应该把哪些 schema 发给模型”。它不发现工具、
    不执行工具，也不代替执行阶段的权限检查。

    ``route`` 的固定顺序是：

    1. 建立候选工具目录，并处理显式 ``all`` 模式；
    2. 从任务文本识别只读、修复、验证、审查和澄清意图；
    3. 合并 Skill、SWE-bench 和外部 MCP 工具的专项约束；
    4. 返回可见 schema、隐藏名称和可写入 Trace 的决策证据。

    当前任务识别是可解释的关键词规则，不是语义分类模型。Router 只缩小模型的能力
    视图；即使模型伪造隐藏 ToolCall，执行管线仍会用 ``allowed_names`` 再次拒绝。
    """

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
        "python_validation": {
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
        "replace_text": {
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
        """为一个 turn 生成模型工具视图，并保留完整的选择证据。

        ``schemas`` 来自 ``ToolGateway.schemas()``，表示当前 Runtime 已注册的候选工具。
        返回的 ``schemas`` 会真正进入模型请求，``allowed_names`` 交给执行管线复核，
        ``dropped_names`` 和 ``metadata`` 则解释“为什么模型看得见或看不见某个工具”。

        这里的规则只决定可见性，不代表授权。写操作即使可见，仍需经过 Hook、权限
        策略、Approval 和 Operation Ledger。
        """

        # region 1. 候选目录：索引 Gateway 已注册的 schema，处理显式全量模式
        # Router 不发现或注册工具。这里保留 schema 原顺序供最终模型请求使用，同时
        # 建立名称索引；后续规则只操作名称集合，不复制或修改 schema。
        task_text = request.task
        candidate_schemas = request.schemas
        current_step = request.step
        agent_name = request.agent_name
        active_skill_tool_names = request.skill_tool_names
        routing_mode = request.mode
        normalized_task_text = (task_text or "").lower()
        schema_by_tool_name = {
            schema.get("name", ""): schema for schema in candidate_schemas
        }
        registered_tool_names = set(schema_by_tool_name)
        if routing_mode not in {"task-aware", "all"}:
            raise ValueError(f"unsupported tool routing mode: {routing_mode}")

        # ``all`` 只表示全部工具对模型可见，执行时仍然要经过权限与安全策略。
        if routing_mode == "all":
            return ToolRoute(
                schemas=list(candidate_schemas),
                allowed_names=set(registered_tool_names),
                reason=(
                    f"mode=all selected={len(candidate_schemas)} dropped=0 "
                    f"step={current_step} agent={agent_name or 'agent'}"
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
                    for name in sorted(registered_tool_names)
                },
            )
        # endregion 1. 候选目录结束

        # region 2. 通用任务意图：先给只读基础能力，再按任务目的逐步扩展
        # 只读判断必须最先完成，因为后面的修复/验证关键词只能扩展候选能力，不能覆盖
        # 用户明确给出的禁止写入约束。
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
        is_read_only_task = any(
            marker in normalized_task_text for marker in read_only_markers
        )

        # “不要修改测试，除非……”约束的是测试文件，不等于整个修复任务只读。
        if is_read_only_task and any(
            marker in normalized_task_text
            for marker in [
                "do not edit tests unless",
                "do not modify tests unless",
                "不要修改测试，除非",
                "不要改测试，除非",
            ]
        ):
            is_read_only_task = False

        # 所有任务先获得仓库发现、读取和搜索能力；ask_human 是独立澄清通道。
        visible_tool_names = {
            name
            for name in registered_tool_names
            if self.DEFAULT_METADATA.get(name, {}).get("capability")
            in {"discover", "inspect", "search"}
        }
        visible_tool_names |= registered_tool_names & {"ask_human"}

        # 修复任务需要“检查 -> 编辑 -> 验证 -> 查看改动”的完整闭环。这里允许模型看见
        # 写工具，真实写入仍然由执行阶段的权限链审批。
        if not is_read_only_task and any(
            token in normalized_task_text
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
            visible_tool_names |= registered_tool_names & {
                "replace_text",
                "write_file",
                "run_command",
                "python_validation",
                "git_status",
                "git_diff",
            }

        # 显式验证任务即使没有命中“修复”，也应获得受限验证能力。
        if not is_read_only_task and any(
            token in normalized_task_text
            for token in ["test", "validate", "验证", "测试", "unittest"]
        ):
            visible_tool_names |= registered_tool_names & {
                "run_command",
                "python_validation",
            }

        # 审查任务关注候选改动和来源文件，不自动获得编辑能力。
        if any(
            token in normalized_task_text
            for token in ["review", "diff", "审查", "回滚"]
        ):
            visible_tool_names |= registered_tool_names & {
                "git_diff",
                "git_status",
                "read_file",
            }

        # 任务明确存在歧义时，确保模型可以通过 durable HITL 协议向人提问。
        if any(
            token in normalized_task_text
            for token in ["clarify", "unclear", "ambiguous", "澄清", "不明确"]
        ):
            visible_tool_names |= registered_tool_names & {"ask_human"}
        # endregion 2. 通用任务意图结束

        # region 3. 专项策略：合并 Skill，再应用只读/SWE-bench/MCP 边界
        # Skill 只能推荐已经注册的工具，不能凭名称创造不存在的能力。
        if active_skill_tool_names:
            visible_tool_names |= registered_tool_names & active_skill_tool_names

        # 再次应用只读约束，确保 Skill 也不能把写入或命令工具加回来。
        if is_read_only_task:
            visible_tool_names -= {"replace_text", "write_file", "run_command"}

        # SWE-bench 使用锚点替换和固定 Python 验证器，隐藏自由命令与整文件覆盖，减少
        # 不可审计操作，同时保留 candidate diff 和工作区状态证据。
        is_swebench_task = (
            "swe-bench" in normalized_task_text
            or "swebench" in normalized_task_text
        )
        if is_swebench_task:
            visible_tool_names -= {"run_command", "write_file"}
            visible_tool_names |= registered_tool_names & {
                "replace_text",
                "python_validation",
                "git_diff",
                "git_status",
            }

        # 外部 MCP 工具没有内置 capability 映射，只在任务明确提及 MCP/工具/策略，或
        # 任务关键词命中工具名称与描述时暴露。这里只判断可见性，不判断副作用风险。
        external_tool_names = registered_tool_names - set(self.DEFAULT_METADATA)
        task_keywords = {
            term
            for term in normalized_task_text.replace("_", " ")
            .replace(".", " ")
            .split()
            if len(term) >= 3
        }
        for tool_name in external_tool_names:
            external_tool_schema = schema_by_tool_name[tool_name]
            tool_search_text = (
                f"{tool_name} {external_tool_schema.get('description', '')}".lower()
            )
            if (
                "mcp" in normalized_task_text
                or "tool" in normalized_task_text
                or "policy" in normalized_task_text
                or any(keyword in tool_search_text for keyword in task_keywords)
            ):
                visible_tool_names.add(tool_name)
        # endregion 3. 专项策略结束

        # region 4. 输出投影：生成模型 schema、执行白名单和可观测证据
        # 兼容兜底：规则未选中任何工具时保持旧行为，暴露全部已注册工具。这是可见性
        # fail-open，不会绕过执行阶段的 Hook 和权限检查。
        if not visible_tool_names:
            visible_tool_names = set(registered_tool_names)

        # 保持 Registry 原始 schema 顺序，使 provider 请求和 Trace 在多次运行间稳定。
        visible_tool_schemas = [
            schema
            for schema in candidate_schemas
            if schema.get("name") in visible_tool_names
        ]
        hidden_tool_names = sorted(registered_tool_names - visible_tool_names)
        routing_reason = (
            f"selected={len(visible_tool_schemas)} "
            f"dropped={len(hidden_tool_names)} step={current_step} "
            f"agent={agent_name or 'agent'} "
            f"skill_tools={len(active_skill_tool_names or set())}"
        )
        if is_swebench_task and "python_validation" in visible_tool_names:
            routing_reason += " swebench_validation=python_validation:pytest"
        return ToolRoute(
            schemas=visible_tool_schemas,
            allowed_names={
                schema.get("name", "") for schema in visible_tool_schemas
            },
            reason=routing_reason,
            dropped_names=hidden_tool_names,
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
                for name in sorted(visible_tool_names)
            },
        )
        # endregion 4. 输出投影结束
