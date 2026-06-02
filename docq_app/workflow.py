from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WorkflowStage(str, Enum):
    INTAKE = "intake"
    FOLLOWUP = "followup"
    TRIAGE = "triage"
    DECISION = "decision"
    HUMAN_REVIEW = "human_review"
    SCHEDULING = "scheduling"
    COMMUNICATION = "communication"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class CaseWorkflowState:
    conversation_id: str
    raw_message: str
    patient_id: str | None = None
    patient_email: str = ""
    patient_phone: str = ""
    actor_role: str = "public"
    known_context: dict[str, Any] = field(default_factory=dict)
    intake_data: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    follow_up_questions: list[str] = field(default_factory=list)
    risk_level: str = "unknown"
    confidence: float = 0.0
    next_action: str = "collect_input"
    assigned_agent: str = "intake"
    human_review_required: bool = False
    retry_count: int = 0
    available_dates: list[dict[str, str]] = field(default_factory=list)
    doctor_matches: list[dict[str, Any]] = field(default_factory=list)
    recommended_doctor: dict[str, Any] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    ml_governance: dict[str, Any] = field(default_factory=dict)
    workflow_trace: list[dict[str, str]] = field(default_factory=list)
    reasoning_trace: list[str] = field(default_factory=list)
    tool_telemetry: list[dict[str, Any]] = field(default_factory=list)
    policy_decision: str = "pending"
    current_stage: WorkflowStage = WorkflowStage.INTAKE
    trace_id: str = ""
    root_event_id: int | None = None
    last_event_id: int | None = None
    causation_depth: int = 0
    replay_branch_id: str = "main"

    def record(self, agent: str, decision: str) -> None:
        self.workflow_trace.append({"agent": agent, "decision": decision})

    def set_stage(self, stage: WorkflowStage) -> None:
        self.current_stage = stage

    def add_reason(self, reason: str) -> None:
        if reason and reason not in self.reasoning_trace:
            self.reasoning_trace.append(reason)
