import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace

from agent_forge.runtime.application.turn_preparation import TurnPreparation
from agent_forge.runtime.application.working_memory import WorkingMemory
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.domain.model import ModelCapabilities


@dataclass
class _ContextReport:
    """测试用最小上下文结果；只承载 TurnPreparation 会读取的字段。"""

    available_tools: list[str]
    permission_summary: str
    selected_files: list[str] = field(default_factory=list)
    retrieved_docs: list[str] = field(default_factory=list)
    working_memory_summary: str = ""
    long_term_memory: list[str] = field(default_factory=list)
    total_chars: int = 0
    max_chars: int = 8_000
    truncated: bool = False
    topic_relation: str = "same"
    inherit_session: bool = True
    dropped_context: list[str] = field(default_factory=list)
    budget_breakdown: dict[str, int] = field(default_factory=dict)
    instruction_evidence: dict[str, object] = field(default_factory=dict)

    def render(self) -> str:
        return self.permission_summary


class _CapturingContextAssembler:
    def __init__(self) -> None:
        self.last_request = None

    def build(self, request):
        self.last_request = request
        return _ContextReport(
            available_tools=[schema["name"] for schema in request.tools],
            permission_summary=request.permission_summary,
        )


class _ToolGateway:
    def __init__(self, names: list[str]) -> None:
        self._schemas = [{"name": name, "arguments": {}} for name in names]

    def schemas(self):
        return list(self._schemas)

    def get(self, name):
        return None

    def execute(self, name, arguments):
        raise AssertionError("prepare_turn must not execute tools")


class _LegacyEnvironment:
    """旧第三方 Adapter：只实现稳定 EnvironmentPort，没有 diff()。"""

    def probe(self):
        return SimpleNamespace(to_dict=lambda: {})

    def render_boundary_summary(self) -> str:
        return "execution_environment mode=legacy"


class _ExplodingDiffEnvironment(_LegacyEnvironment):
    def diff(self) -> str:
        raise AssertionError("final zero-tool turn must not read workspace diff")


class _Trace:
    def __init__(self) -> None:
        self.events = []

    def add(self, step, agent_name, event_type, **payload):
        self.events.append((step, agent_name, event_type, payload))


class _Lifecycle:
    def update_checkpoint(self, update) -> None:
        return None


def _session(task: str, *, max_iterations: int):
    return SimpleNamespace(
        task=task,
        agent_name="TestAgent",
        max_iterations=max_iterations,
        lifecycle=_Lifecycle(),
        messages=[Message(role="user", content=task)],
        observations=[],
        working_memory=WorkingMemory(),
        active_skills=[],
        skill_tool_names=set(),
    )


class TurnPreparationCloseoutTest(unittest.TestCase):
    def _preparation(self, *, environment, context, tools):
        return TurnPreparation(
            config=RuntimeConfig(workspace=".", max_steps=3),
            trace=_Trace(),
            context=context,
            tools=tools,
            environment=environment,
            model_capabilities=ModelCapabilities(),
        )

    def test_legacy_environment_without_diff_keeps_running(self):
        context = _CapturingContextAssembler()
        preparation = self._preparation(
            environment=_LegacyEnvironment(),
            context=context,
            tools=_ToolGateway(["read_file", "replace_text", "create_file"]),
        )

        prepared = preparation.prepare_turn(
            _session("Resolve this SWE-bench coding issue.", max_iterations=3),
            step=2,
        )

        self.assertIn("read_file", prepared.allowed_tool_names)
        self.assertNotIn("repair commit phase", context.last_request.permission_summary)

    def test_read_only_closeout_never_requests_a_write(self):
        context = _CapturingContextAssembler()
        preparation = self._preparation(
            environment=_LegacyEnvironment(),
            context=context,
            tools=_ToolGateway(
                [
                    "read_file",
                    "grep_search",
                    "replace_text",
                    "create_file",
                    "git_diff",
                ]
            ),
        )

        prepared = preparation.prepare_turn(
            _session(
                "Read only: inspect this SWE-bench issue; do not modify files.",
                max_iterations=3,
            ),
            step=2,
        )

        self.assertNotIn("replace_text", prepared.allowed_tool_names)
        self.assertNotIn("create_file", prepared.allowed_tool_names)
        self.assertIn("read-only closeout", context.last_request.permission_summary)
        self.assertIn("do not modify files", prepared.messages_for_llm[-1].content)

    def test_final_turn_has_zero_tools_and_does_not_read_diff(self):
        context = _CapturingContextAssembler()
        preparation = self._preparation(
            environment=_ExplodingDiffEnvironment(),
            context=context,
            tools=_ToolGateway(["read_file", "replace_text", "create_file"]),
        )

        prepared = preparation.prepare_turn(
            _session("Resolve this SWE-bench coding issue.", max_iterations=3),
            step=3,
        )

        self.assertEqual(prepared.allowed_tool_names, set())
        self.assertEqual(prepared.schemas, [])
        self.assertIn("Tool execution is closed", prepared.messages_for_llm[-1].content)


if __name__ == "__main__":
    unittest.main()
