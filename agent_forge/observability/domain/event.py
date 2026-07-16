from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, TypeAlias

TraceRecord: TypeAlias = dict[str, Any]

TraceEventType: TypeAlias = Literal[
    "action",
    "agent_stage_end",
    "agent_stage_start",
    "artifact_created",
    "clarification_decision",
    "context_assembly",
    "context_overflow_recovery",
    "context_window",
    "error",
    "evidence_collected",
    "execution_environment",
    "fanout_batch_done",
    "fanout_done",
    "fanout_start",
    "final_answer",
    "finalizer_error",
    "guardrail_check",
    "hook_check",
    "human_approval",
    "human_input_cancelled",
    "human_input_requested",
    "human_input_response_loaded",
    "llm_call",
    "memory_recall",
    "multi_agent_done",
    "multi_agent_start",
    "observation",
    "operation_ledger",
    "permission_check",
    "recovery_decision",
    "resume_state_loaded",
    "review_decision",
    "revision_round",
    "skill_selection",
    "stop_hooks",
    "task_state_checkpoint",
    "tool_call",
    "tool_calls_bounded",
    "tool_calls_deferred_for_human_input",
    "tool_observation",
    "validation_evidence",
    "verifier_result",
]

RESERVED_EVENT_FIELDS = {
    "run_id",
    "step",
    "agent_name",
    "event_type",
    "duration_ms",
    "success",
    "error",
}


@dataclass(frozen=True)
class TraceEvent:

    run_id: str
    step: int
    agent_name: str
    event_type: TraceEventType
    duration_ms: int
    success: bool = True
    error: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> TraceRecord:

        overlap = RESERVED_EVENT_FIELDS.intersection(self.data)
        if overlap:
            names = ", ".join(sorted(overlap))
            raise ValueError(f"trace payload cannot overwrite envelope fields: {names}")
        return {
            "run_id": self.run_id,
            "step": self.step,
            "agent_name": self.agent_name,
            "event_type": self.event_type,
            "duration_ms": self.duration_ms,
            "success": self.success,
            "error": self.error,
            **dict(self.data),
        }
