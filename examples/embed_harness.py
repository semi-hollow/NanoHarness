"""只依赖 Public API 的最小嵌入示例。"""

from agent_forge import Harness, HarnessConfig, HarnessExtensions
from agent_forge.extensions import (
    AgentResponse,
    Message,
    ModelPort,
    Observation,
    RuntimeEvent,
    Tool,
    ToolArguments,
    ToolCall,
    ToolRegistry,
    ToolSchema,
)


class EventPrinter:
    """示例控制面只消费脱敏 RuntimeEvent，不读取内部 trace 对象。"""

    def on_event(self, event: RuntimeEvent) -> None:
        if event.name in {"run.started", "tool.started", "run.completed"}:
            print(f"event={event.name} sequence={event.sequence}")


class RepositoryStatusTool(Tool):
    """示例自定义 Tool；真实项目可在这里调用自己的服务。"""

    name = "repository_status"
    description = "Return a small repository status summary."

    def schema(self) -> ToolSchema:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": {},
            "required": [],
        }

    def execute(self, arguments: ToolArguments) -> Observation:
        return Observation(self.name, True, "workspace is ready")


class TwoTurnModel(ModelPort):
    """示例 ModelPort：第一轮调用工具，第二轮交付最终答案。"""

    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> AgentResponse:
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                None,
                [ToolCall("status-1", "repository_status", {})],
            )
        return AgentResponse("Repository status inspected through NanoHarness.", [])


def main() -> None:
    registry = ToolRegistry()
    registry.register(RepositoryStatusTool())
    harness = Harness(
        model=TwoTurnModel(),
        tools=registry,
        config=HarnessConfig(
            workspace=".",
            output_root=".agent_forge/examples",
            approval_mode="locked",
        ),
        extensions=HarnessExtensions(event_listeners=(EventPrinter(),)),
    )
    result = harness.run("Inspect repository status and report the result.")
    print(result.status.value)
    print(result.artifact_dir)


if __name__ == "__main__":
    main()
