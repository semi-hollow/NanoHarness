"""Benchmark 结束后的失败分析服务。

这里不是模型可调用的 ``PythonValidationTool``。本服务只读取已经生成的 usage/trace，调用
领域层 Failure Taxonomy，并把诊断结果写回 ``BenchCaseResult``。
"""

from __future__ import annotations

from agent_forge.bench.domain.failure_taxonomy import (
    FailureDiagnosis,
    classify_case_result,
)
from agent_forge.bench.domain.models import BenchCaseResult
from agent_forge.bench.ports.benchmark import CaseEvidenceReader


class BenchFailureAnalyzer:
    """连接“读取运行证据”和“按 Taxonomy 分类”两个步骤。

    可类比支付系统的差错归因服务：它读取已落盘事实，再套用稳定分类规则；不会回到
    AgentLoop 执行工具，也不会修改候选代码。
    """

    def __init__(self, evidence_reader: CaseEvidenceReader) -> None:
        self._evidence_reader = evidence_reader

    # 主要入口：读取最终证据，并把唯一失败诊断写回 benchmark case result。
    def enrich_result_with_failure_diagnosis(
        self,
        result: BenchCaseResult,
    ) -> BenchCaseResult:
        """补齐报告和 case study 后续消费的诊断字段。"""

        diagnosis = self.classify_case_failure(result)
        result.failure_class = diagnosis.failure_class
        result.diagnosis = diagnosis.summary
        result.diagnosis_evidence = diagnosis.evidence
        result.next_actions = diagnosis.next_actions
        result.diagnosis_source = diagnosis.source
        result.diagnosis_rule_id = diagnosis.rule_id
        result.diagnosis_taxonomy_version = diagnosis.taxonomy_version
        return result

    def classify_case_failure(self, result: BenchCaseResult) -> FailureDiagnosis:
        """读取 usage/trace，并返回纯领域规则生成的诊断。"""

        return classify_case_result(
            result,
            self._evidence_reader.load_usage(result),
            self._evidence_reader.load_trace(result),
        )
