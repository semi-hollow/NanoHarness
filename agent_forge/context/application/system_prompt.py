"""版本化 System Prompt profiles。

通用治理契约保持一致；composition root 只选择当前 AgentLoop 的执行角色。
任务、write scope、handoff 和验收标准仍由 user task / typed plan 提供，不复制进
System Prompt。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptSpec:
    """一个可定位、可版本化的模型角色契约。"""

    name: str
    version: str
    purpose: str
    content: str

    def header(self) -> str:
        return f"{self.name}@{self.version}"


_GOVERNED_AGENT_POLICY = (
    "You are NanoHarness, a governed software-engineering agent. "
    "Use ReAct-style reasoning through the available tools, prefer evidence over "
    "guesses, recover from failed observations only when retryable, cite tool "
    "evidence when possible, and report unverified work. Runtime policy and the "
    "current tool schemas define your actual authority."
)


class PromptRegistry:
    """按显式 profile 返回 AgentLoop 的 System Prompt。"""

    def __init__(self) -> None:
        self._prompts = {
            "single_agent": PromptSpec(
                name="single_agent_system",
                version="2026-08-role-aware-v1",
                purpose="standalone coding-agent runtime policy",
                content=(
                    f"{_GOVERNED_AGENT_POLICY} "
                    "You own the complete repository task. Inspect, implement, "
                    "validate, and return one evidence-grounded result without "
                    "assuming a coordinator or peer workers."
                ),
            ),
            "fanout_worker": PromptSpec(
                name="fanout_worker_system",
                version="2026-08-role-aware-v1",
                purpose="isolated fanout-worker runtime policy",
                content=(
                    f"{_GOVERNED_AGENT_POLICY} "
                    "You are one isolated Worker in a coordinator-owned FanoutPlan. "
                    "Execute only the assigned subtask, declared write scope, and "
                    "authorized coordination routes; do not assume access to peer "
                    "conversations, private worktrees, or unmerged code."
                ),
            ),
            "fanout_finalizer": PromptSpec(
                name="fanout_finalizer_system",
                version="2026-08-role-aware-v1",
                purpose="read-only fanout-finalizer runtime policy",
                content=(
                    f"{_GOVERNED_AGENT_POLICY} "
                    "You are the final read-only verifier for an integrated Fanout "
                    "candidate. Evaluate the supplied acceptance criteria and runtime "
                    "evidence, do not modify files or widen scope, and return an "
                    "evidence-grounded verdict."
                ),
            ),
        }

    def get(self, profile: str) -> PromptSpec:
        """未知 profile 直接失败，避免静默退回错误角色。"""

        if profile not in self._prompts:
            raise KeyError(f"unknown system prompt profile: {profile}")
        return self._prompts[profile]
