from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClarificationDecision:

    action: str
    confidence: float
    reason: str
    question: str = ""
    missing_fields: list[str] = field(default_factory=list)

    def needs_user_input(self) -> bool:

        return self.action == "ask"


class ClarificationPolicy:

    vague_references = {
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
    coding_intents = {
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
    explicit_targets = {
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
    unsupported_topics = {"训练模型", "微调模型", "视频生成", "多模态训练", "上线支付", "真实转账"}

    def decide(self, task: str) -> ClarificationDecision:

        text = (task or "").strip()
        lowered = text.lower()
        if not text:
            return ClarificationDecision(
                "ask",
                0.0,
                "empty task",
                "你希望 Agent 具体完成什么任务？",
                ["task"],
            )

        if any(topic in text for topic in self.unsupported_topics):
            return ClarificationDecision(
                "refuse",
                0.2,
                "task is outside this local CodingAgent harness boundary",
                "",
                ["supported_runtime_boundary"],
            )

        has_intent = any(intent in lowered or intent in text for intent in self.coding_intents)
        has_target = any(target in text for target in self.explicit_targets)
        has_vague_reference = any(token in lowered or token in text for token in self.vague_references)

        missing = []

        # 对“看这个项目”这类仓库导览任务，Agent 可以先检查项目，再判断是否确实需要追问。

        if has_vague_reference and not has_target:
            missing.append("referenced_object")

        if missing:
            return ClarificationDecision(
                "ask",
                0.35,
                "task contains an action but lacks a concrete target",
                "你希望我操作哪个文件、模块或场景？请给出路径或明确目标。",
                missing,
            )

        return ClarificationDecision("proceed", 0.85 if has_target else 0.65, "task is specific enough to start")
