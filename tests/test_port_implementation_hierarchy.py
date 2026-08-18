"""防止正式 Adapter 退回只能靠方法形状猜测其 Port。"""

import unittest

from agent_forge._harness_support import TrackingTaskStateRepository
from agent_forge.bench.adapters.artifact_files import FileBenchArtifacts
from agent_forge.bench.adapters.campaign_files import (
    FileCampaignArtifacts,
    GitSourceIdentity,
)
from agent_forge.bench.adapters.case_evidence import JsonCaseEvidenceReader
from agent_forge.bench.adapters.case_runtime import LocalCaseExecutor
from agent_forge.bench.adapters.dataset import SwebenchCaseSource
from agent_forge.bench.adapters.official_evaluator import SwebenchOfficialEvaluator
from agent_forge.bench.ports import (
    BenchArtifactPort,
    CampaignArtifactPort,
    CaseEvidenceReader as BenchCaseEvidenceReader,
    CaseExecutorPort,
    CaseSourcePort,
    OfficialEvaluatorPort,
    SourceIdentityPort,
)
from agent_forge.context.adapters.memory_json import JsonLongTermMemoryRepository
from agent_forge.context.application.memory_service import LongTermMemoryService
from agent_forge.context.ports import (
    LongTermMemoryRecallPort,
    LongTermMemoryRepository,
)
from agent_forge.control import RunController
from agent_forge.evaluation.adapters.json_files import (
    JsonCaseEvidenceReader as EvaluationJsonCaseEvidenceReader,
)
from agent_forge.evaluation.ports import (
    CaseEvidenceReader as EvaluationCaseEvidenceReader,
)
from agent_forge.models.gateway import ModelGateway
from agent_forge.multi_agent.adapters.fanout_files import FanoutFileRepository
from agent_forge.multi_agent.adapters.git_workspace import GitFanoutWorkspace
from agent_forge.multi_agent.adapters.local_worker import LocalAgentWorkerAdapter
from agent_forge.multi_agent.ports import (
    FanoutArtifactPort,
    FanoutWorkerPort,
    FanoutWorkspacePort,
)
from agent_forge.observability.adapters.json_trace import JsonTraceRecorder
from agent_forge.observability.adapters.otel import OpenTelemetryEventListener
from agent_forge.observability.adapters.streaming import StreamingEventSink
from agent_forge.observability.ports.events import RuntimeEventListener
from agent_forge.operator_console.adapters.session_catalog_json import (
    JsonTaskSessionCatalog,
)
from agent_forge.operator_console.events import RuntimeEventBuffer
from agent_forge.operator_console.ports.session_catalog import TaskSessionCatalogPort
from agent_forge.runtime.adapters.approval_json import JsonApprovalRepository
from agent_forge.runtime.adapters.context_assembler import (
    RepositoryTurnSystemContextAssembler,
)
from agent_forge.runtime.adapters.human_input_json import JsonHumanInputRepository
from agent_forge.runtime.adapters.operation_ledger_json import (
    JsonOperationLedgerRepository,
)
from agent_forge.runtime.adapters.run_control_noop import NoopRunControl
from agent_forge.runtime.adapters.task_state_json import JsonTaskStateRepository
from agent_forge.runtime.execution_environment import ExecutionEnvironment
from agent_forge.runtime.hooks import HookManager
from agent_forge.runtime.llm_client import LLMClient
from agent_forge.runtime.ports import (
    ApprovalRepository,
    TurnSystemContextAssemblerPort,
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
from agent_forge.skills.registry import SkillRegistry
from agent_forge.tools.registry import ToolRegistry
from agent_forge.workbench.adapters.evidence_files import FileEvidenceCatalog
from agent_forge.workbench.adapters.experiment_files import FileExperimentCatalog
from agent_forge.workbench.ports import EvidenceCatalogPort, ExperimentCatalogPort


FORMAL_IMPLEMENTATIONS = (
    (JsonLongTermMemoryRepository, LongTermMemoryRepository),
    (LongTermMemoryService, LongTermMemoryRecallPort),
    (ExecutionEnvironment, EnvironmentPort),
    (LLMClient, ModelPort),
    (ModelGateway, LLMClient),
    (JsonTraceRecorder, EventSink),
    (StreamingEventSink, EventSink),
    (OpenTelemetryEventListener, RuntimeEventListener),
    (RuntimeEventBuffer, RuntimeEventListener),
    (TrackingTaskStateRepository, TaskStateRepository),
    (JsonTaskStateRepository, TaskStateRepository),
    (JsonHumanInputRepository, HumanInputRepository),
    (JsonApprovalRepository, ApprovalRepository),
    (JsonOperationLedgerRepository, OperationLedgerRepository),
    (RepositoryTurnSystemContextAssembler, TurnSystemContextAssemblerPort),
    (RunController, RunControlPort),
    (NoopRunControl, RunControlPort),
    (HookManager, HookPort),
    (SkillRegistry, SkillSelectorPort),
    (ToolRegistry, ToolGateway),
    (JsonTaskSessionCatalog, TaskSessionCatalogPort),
    (SwebenchCaseSource, CaseSourcePort),
    (LocalCaseExecutor, CaseExecutorPort),
    (SwebenchOfficialEvaluator, OfficialEvaluatorPort),
    (JsonCaseEvidenceReader, BenchCaseEvidenceReader),
    (FileBenchArtifacts, BenchArtifactPort),
    (GitSourceIdentity, SourceIdentityPort),
    (FileCampaignArtifacts, CampaignArtifactPort),
    (EvaluationJsonCaseEvidenceReader, EvaluationCaseEvidenceReader),
    (FileEvidenceCatalog, EvidenceCatalogPort),
    (FileExperimentCatalog, ExperimentCatalogPort),
    (GitFanoutWorkspace, FanoutWorkspacePort),
    (FanoutFileRepository, FanoutArtifactPort),
    (LocalAgentWorkerAdapter, FanoutWorkerPort),
)


class PortImplementationHierarchyTest(unittest.TestCase):
    def test_formal_implementations_name_their_primary_port(self) -> None:
        """PyCharm Hierarchy 应能从正式 Port 直接跳到项目内实现。"""

        for implementation, port in FORMAL_IMPLEMENTATIONS:
            with self.subTest(
                implementation=implementation.__name__,
                port=port.__name__,
            ):
                self.assertIn(port, implementation.__bases__)


if __name__ == "__main__":
    unittest.main()
