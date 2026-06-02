from __future__ import annotations

from ..appointments import create_emergency_escalation, record_workflow_event
from ..notifications import create_notification
from ..workflow import CaseWorkflowState, WorkflowStage


class EmergencyEscalationAgent:
    name = "emergency-escalation-agent"

    def process(self, state: CaseWorkflowState) -> CaseWorkflowState:
        state.set_stage(WorkflowStage.HUMAN_REVIEW)
        risk = state.analysis.get("risk_explanation", {})
        message = (
            "DOCQ ALERT: Patient reported emergency-risk symptoms. "
            "Immediate medical evaluation is recommended. This is not a diagnosis."
        )
        escalation_id = create_emergency_escalation(
            appointment_id=None,
            workflow_id=state.conversation_id,
            patient_name=str(state.analysis.get("patient_name") or "Patient"),
            patient_phone=state.patient_phone,
            patient_email=state.patient_email,
            risk_level=str(risk.get("risk_level") or state.analysis.get("urgency") or "EMERGENCY"),
            risk_score=float(risk.get("risk_score") or state.analysis.get("priority_score") or 0.0),
            summary=message,
            status="active",
        )
        create_notification(
            None,
            "doctor",
            str(state.analysis.get("doctor_name") or "Emergency Review Team"),
            "dashboard",
            message,
            "visible",
            correlation_id=f"{state.conversation_id}:emergency-doctor-alert",
            provider_metadata={"workflow_id": state.conversation_id, "risk": risk, "escalation_id": escalation_id},
            message_category="emergency_escalation",
        )
        if state.patient_phone:
            create_notification(
                None,
                "patient",
                "Emergency Patient",
                "sms",
                message,
                "queued",
                correlation_id=f"{state.conversation_id}:emergency-patient-alert",
                provider_metadata={"workflow_id": state.conversation_id, "escalation_id": escalation_id},
                message_category="emergency_escalation",
            )
        record_workflow_event(
            state.conversation_id,
            trace_id=state.trace_id or state.conversation_id,
            correlation_id=state.conversation_id,
            stage="emergency-escalation",
            agent=self.name,
            action="create_emergency_escalation",
            decision="emergency_escalation",
            confidence=float(state.confidence or 0.0),
            reasons=state.reasoning_trace,
            payload={"escalation_id": escalation_id, "risk": risk, "message": message},
        )
        state.analysis["emergency_escalation"] = {
            "id": escalation_id,
            "status": "active",
            "message": message,
            "actions": ["doctor_dashboard_alert", "patient_sms_alert" if state.patient_phone else "patient_dashboard_guidance"],
        }
        state.next_action = "complete"
        state.assigned_agent = self.name
        state.record(self.name, f"created emergency escalation {escalation_id}")
        return state
