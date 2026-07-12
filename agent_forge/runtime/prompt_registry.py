from dataclasses import dataclass


@dataclass(frozen=True)
class PromptSpec:
    """Versioned prompt entry used by runtime components."""

    name: str
    version: str
    purpose: str
    content: str

    def header(self) -> str:
        """Return a compact prompt id for trace and reports."""

        return f"{self.name}@{self.version}"


class PromptRegistry:
    """Central place for runtime prompts.

    Product teams usually store prompts in config or a prompt platform. This
    lightweight registry gives the project the same engineering shape: prompts
    are named, versioned, auditable, and not hidden inside random call sites.
    """

    def __init__(self) -> None:
        """Load built-in prompts."""

        self._prompts = {
            "agent_system": PromptSpec(
                name="agent_system",
                version="2026-06-core",
                purpose="single-agent coding runtime policy",
                content=(
                    "You are Agent Forge, a controlled coding-agent runtime. "
                    "Use ReAct-style reasoning through tools, prefer evidence over guesses, "
                    "recover from failed observations when retryable, cite tool evidence when possible, "
                    "and report unverified work."
                ),
            )
        }

    def get(self, name: str) -> PromptSpec:
        """Return one prompt by stable name."""

        if name not in self._prompts:
            raise KeyError(f"unknown prompt: {name}")
        return self._prompts[name]
