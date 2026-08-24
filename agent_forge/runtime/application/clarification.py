"""任务进入 AgentLoop 前的澄清边界。

可类比为 Java 服务入口的参数校验器：它只决定“继续、请求补充信息还是拒绝”，
不执行任务，也不保存人工回答。人工问题的持久化由 HumanInputRepository 负责。

系统角色：在任何 Model Step 前，用可解释规则区分 proceed / ask / refuse；它只生成
``ClarificationDecision``，RunPreparation/RunLifecycle 才创建 durable human barrier。
输入：root task；输出：typed decision。

折叠导航：1 decision contract；2 policy vocabulary；3 evaluation flow。
"""

from dataclasses import dataclass, field


# region 1. Clarification 决策契约
@dataclass(frozen=True, kw_only=True)
class ClarificationDecision:
    """一次任务澄清判断；关键字构造避免混淆 action、reason 和 question。"""

    action: str
    confidence: float
    reason: str
    question: str = ""
    missing_fields: list[str] = field(default_factory=list)

    def needs_user_input(self) -> bool:
        return self.action == "ask"
# endregion 1. Decision contract 结束


class ClarificationPolicy:
    """根据任务文本决定 AgentLoop 能否开始。"""

    # region 2. 可解释 vocabulary：仅标记当前支持的模糊、目标与越界信号
    VAGUE_REFERENCES = {
        "这个",
        "那个",
        "它",
        "按老样子",
        "随便",
        "处理一下",
        "fix it",
        "do it",
        "make it work",
        "same as before",
    }
    CODING_INTENTS = {
        "fix",
        "repair",
        "resolve",
        "implement",
        "add",
        "modify",
        "patch",
        "refactor",
        "修复",
        "实现",
        "修改",
        "补充",
        "新增",
        "优化",
    }
    EXPLICIT_TARGETS = {
        ".py",
        ".md",
        ".json",
        ".toml",
        "/",
        "agent_forge/",
        "tests/",
        "project",
        "repo",
        "repository",
        "项目",
        "代码库",
    }
    UNSUPPORTED_TOPICS = {
        "训练模型",
        "微调模型",
        "视频生成",
        "多模态训练",
        "上线支付",
        "真实转账",
    }
    # endregion 2. Policy vocabulary 结束

    # region 3. Evaluation flow：empty/refuse/ask/proceed 四个显式出口
    # 主要入口：在创建运行状态前，决定继续、请求补充信息或拒绝。
    def evaluate_task(self, task: str) -> ClarificationDecision:
        """返回任务的澄清决策，不修改外部状态。"""

        text = (task or "").strip()
        normalized_task = text.lower()
        if not text:
            return ClarificationDecision(
                action="ask",
                confidence=0.0,
                reason="empty task",
                question="你希望 Agent 具体完成什么任务？",
                missing_fields=["task"],
            )

        # 明确超出本地 Coding Harness 的任务直接拒绝，不把它包装成澄清问题。
        if any(topic in text for topic in self.UNSUPPORTED_TOPICS):
            return ClarificationDecision(
                action="refuse",
                confidence=0.2,
                reason="task is outside this local CodingAgent harness boundary",
                missing_fields=["supported_runtime_boundary"],
            )

        has_target = any(target in text for target in self.EXPLICIT_TARGETS)
        has_vague_reference = any(
            token in normalized_task or token in text for token in self.VAGUE_REFERENCES
        )

        missing_fields: list[str] = []

        # 对“看这个项目”这类仓库导览任务，Agent 可先检查项目，再判断是否需要补充信息。

        if has_vague_reference and not has_target:
            missing_fields.append("referenced_object")

        if missing_fields:
            return ClarificationDecision(
                action="ask",
                confidence=0.35,
                reason="task contains an action but lacks a concrete target",
                question="你希望我操作哪个文件、模块或场景？请给出路径或明确目标。",
                missing_fields=missing_fields,
            )

        return ClarificationDecision(
            action="proceed",
            confidence=0.85 if has_target else 0.65,
            reason="task is specific enough to start",
        )
    # endregion 3. Evaluation flow 结束
