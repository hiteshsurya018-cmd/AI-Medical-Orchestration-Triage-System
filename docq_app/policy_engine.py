from __future__ import annotations

from .contracts import PolicyDecision
from .pydantic_compat import model_dump
from .workflow import CaseWorkflowState, WorkflowStage


class PolicyEngine:
    def evaluate(self, state: CaseWorkflowState) -> CaseWorkflowState:
        analysis = state.analysis
        urgency = str(analysis.get("urgency", "")).strip()
        severity = str(analysis.get("severity", "")).strip()
        confidence = float(analysis.get("confidence", 0.0) or 0.0)
        patient_age = state.intake_data.get("patient_age")
        history_loaded = bool(state.known_context.get("history_loaded"))

        reasons: list[str] = []
        decision = "autonomous_booking"

        if urgency == "Emergency":
            decision = "emergency_escalation"
            reasons.append("emergency-level urgency detected")
        elif confidence < 70.0:
            decision = "human_review"
            reasons.append("triage confidence below autonomous threshold")
        elif severity in {"High", "Emergency"}:
            decision = "human_review"
            reasons.append("high clinical severity requires oversight")

        if patient_age is not None and int(patient_age) >= 65:
            reasons.append("senior patient risk profile")
        if history_loaded:
            reasons.append("longitudinal medical history available")
        if analysis.get("history_flags"):
            reasons.append("chronic risk factors present")

        typed_decision = PolicyDecision(
            action=decision,
            confidence=confidence,
            reasons=reasons,
            human_review_required=decision in {"human_review", "emergency_escalation"},
        )
        state.policy_decision = typed_decision.action
        state.human_review_required = typed_decision.human_review_required
        for reason in reasons:
            state.add_reason(reason)
        state.analysis["policy_contract"] = model_dump(typed_decision)

        if decision == "emergency_escalation":
            state.next_action = "emergency_escalation"
            state.set_stage(WorkflowStage.HUMAN_REVIEW)
        elif decision == "human_review":
            state.next_action = "human_review"
            state.set_stage(WorkflowStage.HUMAN_REVIEW)
        else:
            state.next_action = "autonomous_booking"
            state.set_stage(WorkflowStage.SCHEDULING)

        state.record(
            "policy-engine",
            f"decision={decision} confidence={confidence} reasons={'; '.join(reasons) or 'none'}",
        )
        return state
