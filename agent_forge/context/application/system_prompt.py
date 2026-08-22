from dataclasses import dataclass


@dataclass(frozen=True)
class PromptSpec:

    name: str
    version: str
    purpose: str
    content: str

    def header(self) -> str:

        return f"{self.name}@{self.version}"


class PromptRegistry:

    def __init__(self) -> None:

        self._prompts = {
            "agent_system": PromptSpec(
                name="agent_system",
                version="2026-06-core",
                purpose="single-agent coding runtime policy",
                content=(
                    "You are NanoHarness, a governed software-engineering agent. "
                    "Use ReAct-style reasoning through tools, prefer evidence over guesses, "
                    "recover from failed observations when retryable, cite tool evidence when possible, "
                    "and report unverified work."
                ),
            )
        }

    def get(self, name: str) -> PromptSpec:

        if name not in self._prompts:
            raise KeyError(f"unknown prompt: {name}")
        return self._prompts[name]
