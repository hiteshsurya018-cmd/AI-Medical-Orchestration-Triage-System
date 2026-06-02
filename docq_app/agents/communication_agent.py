from __future__ import annotations

from ..orchestration import build_conversation_payload, detect_intent
from ..workflow import CaseWorkflowState, WorkflowStage


class CommunicationAgent:
    name = "communication-agent"

    def process(self, state: CaseWorkflowState, *, awaiting_age: bool = False) -> CaseWorkflowState:
        state.set_stage(WorkflowStage.COMMUNICATION)
        intent = detect_intent(state.raw_message, awaiting_age=awaiting_age)
        conversation_payload = build_conversation_payload(
            conversation_id=state.conversation_id,
            patient_id=state.patient_id,
            intent=intent,
            symptoms=str(state.analysis.get("symptoms", "")),
            analysis=state.analysis,
        )
        state.record(self.name, "prepared patient-facing workflow response")
        state.analysis["patient_message"] = conversation_payload["patient_message"]
        state.analysis["conversation_payload"] = conversation_payload
        state.analysis["ui_actions"] = conversation_payload["ui_actions"]
        state.analysis["workflow_state"] = conversation_payload["workflow_state"]
        state.analysis["workflow_trace"] = state.workflow_trace
        state.analysis["reasoning_trace"] = state.reasoning_trace
        state.analysis["policy_decision"] = state.policy_decision
        state.analysis["current_stage"] = WorkflowStage.COMPLETED.value
        state.next_action = "complete"
        state.assigned_agent = self.name
        state.set_stage(WorkflowStage.COMPLETED)
        return state
