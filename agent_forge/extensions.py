"""NanoHarness 承诺稳定的扩展契约。

实现这些 Protocol 不需要继承框架基类。具体 application、adapter 和 wiring 模块仍是
内部实现，不属于兼容性承诺。
"""

from agent_forge.context.ports import (
    LongTermMemoryRecallPort,
    LongTermMemoryRepository,
)
from agent_forge.context.instructions import (
    InstructionResolution,
    InstructionResolutionRequest,
    InstructionSource,
    resolve_instructions,
)
from agent_forge.control import RunController
from agent_forge.hooks import RuntimeHook
from agent_forge.harness_contracts import EventSinkFactory
from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.observability.adapters import (
    EventStreamPolicy,
    OpenTelemetryEventListener,
    OpenTelemetryPolicy,
)
from agent_forge.observability.domain import RuntimeEvent
from agent_forge.observability.ports import RuntimeEventListener
from agent_forge.runtime.domain.conversation import (
    AgentResponse,
    Message,
    Observation,
    ToolCall,
)
from agent_forge.runtime.domain.governance import (
    HookContext,
    HookDecision,
    HookDecisionType,
    ModelHookContext,
)
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.domain.run_control import RunControlKind, RunControlSignal
from agent_forge.runtime.ports import (
    ApprovalRepository,
    ContextAssemblerPort,
    EnvironmentPort,
    EventSink,
    HookPort,
    HumanInputRepository,
    ModelPort,
    OperationLedgerRepository,
    RunControlPort,
    SkillSelectorPort,
    TaskStateRepository,
    ToolGateway,
)
from agent_forge.tools.base import Tool
from agent_forge.tools.registry import ToolRegistry
from agent_forge.skills import SkillCatalogEntry, SkillSpec

__all__ = [
    "AgentResponse",
    "ApprovalRepository",
    "ContextAssemblerPort",
    "EnvironmentPort",
    "EventSink",
    "EventSinkFactory",
    "EventStreamPolicy",
    "HookPort",
    "HookContext",
    "HookDecision",
    "HookDecisionType",
    "HumanInputRepository",
    "InstructionResolution",
    "InstructionResolutionRequest",
    "InstructionSource",
    "LongTermMemoryRecallPort",
    "LongTermMemoryRepository",
    "Message",
    "ModelCapabilities",
    "ModelHookContext",
    "ModelPort",
    "Observation",
    "OpenTelemetryEventListener",
    "OpenTelemetryPolicy",
    "OperationLedgerRepository",
    "RunControlKind",
    "RunController",
    "RunControlPort",
    "RunControlSignal",
    "RuntimeEvent",
    "RuntimeEventListener",
    "RuntimeHook",
    "SkillSelectorPort",
    "SkillCatalogEntry",
    "SkillSpec",
    "TaskStateRepository",
    "Tool",
    "ToolArguments",
    "ToolCall",
    "ToolGateway",
    "ToolRegistry",
    "ToolSchema",
    "resolve_instructions",
]
