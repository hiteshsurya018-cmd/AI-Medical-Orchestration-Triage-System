from __future__ import annotations

from .appointments import record_tool_execution, record_workflow_event
from typing import Any

from .agents import CommunicationAgent, EmergencyEscalationAgent, IntakeAgent, MemoryAgent, QuestionnaireAgent, RiskAssessmentAgent, SchedulingAgent, VitalsAgent
from .policy_engine import PolicyEngine
from .workflow import CaseWorkflowState, WorkflowStage


class CaseWorkflowEngine:
    def __init__(self) -> None:
        self.memory_agent = MemoryAgent()
        self.intake_agent = IntakeAgent()
        self.questionnaire_agent = QuestionnaireAgent()
        self.vitals_agent = VitalsAgent()
        self.risk_agent = RiskAssessmentAgent()
        self.policy_engine = PolicyEngine()
        self.emergency_escalation_agent = EmergencyEscalationAgent()
        self.scheduling_agent = SchedulingAgent()
        self.communication_agent = CommunicationAgent()

    def _log_event(self, state: CaseWorkflowState, *, agent: str, action: str, decision: str = "") -> None:
        payload = {}
        if agent in {self.risk_agent.name, "policy-engine"}:
            payload = dict(state.analysis.get("ml_event_payload", {}))
        event_id = record_workflow_event(
            state.conversation_id,
            trace_id=state.trace_id or state.conversation_id,
            correlation_id=state.conversation_id,
            causation_id=state.last_event_id,
            parent_event_id=state.last_event_id,
            root_event_id=state.root_event_id,
            causation_depth=state.causation_depth,
            replay_branch_id=state.replay_branch_id,
            stage=state.current_stage.value,
            agent=agent,
            action=action,
            decision=decision or state.policy_decision,
            confidence=state.confidence,
            reasons=state.reasoning_trace,
            payload=payload,
        )
        state.last_event_id = event_id
        state.root_event_id = state.root_event_id or event_id
        state.causation_depth += 1

    def run_intake(
        self,
        *,
        conversation_id: str,
        raw_message: str,
        patient_id: str | None,
        patient_email: str,
        patient_phone: str,
        actor_role: str,
        profile,
        stored_age: int | None,
        stored_history: str,
        awaiting_age: bool = False,
        prior_symptoms: str = "",
        questionnaire_payload: dict[str, Any] | None = None,
        require_questionnaire: bool = False,
        vitals_payload: dict[str, Any] | None = None,
    ) -> CaseWorkflowState:
        state = CaseWorkflowState(
            conversation_id=conversation_id,
            raw_message=raw_message,
            patient_id=patient_id,
            patient_email=patient_email,
            patient_phone=patient_phone,
            actor_role=actor_role,
            trace_id=conversation_id,
        )
        self.memory_agent.hydrate(state, profile=profile, stored_age=stored_age, stored_history=stored_history)
        self._log_event(state, agent=self.memory_agent.name, action="hydrate_context")
        if awaiting_age and prior_symptoms:
            state.intake_data["symptoms"] = prior_symptoms
        self.intake_agent.process(state, awaiting_age=awaiting_age)
        self._log_event(state, agent=self.intake_agent.name, action="process_intake")
        if state.next_action == "collect_missing_info":
            state.analysis["policy_decision"] = "follow_up_questions"
            state.analysis["reasoning_trace"] = state.reasoning_trace
            state.analysis["current_stage"] = state.current_stage.value
            return state
        if questionnaire_payload:
            self.questionnaire_agent.hydrate_answers(state, questionnaire_payload)
            self._log_event(state, agent=self.questionnaire_agent.name, action="hydrate_questionnaire")
        elif require_questionnaire:
            self.questionnaire_agent.prepare(state)
            self._log_event(state, agent=self.questionnaire_agent.name, action="prepare_questionnaire")
            if state.next_action == "collect_clinical_questionnaire":
                state.analysis["policy_decision"] = "clinical_questionnaire"
                state.analysis["reasoning_trace"] = state.reasoning_trace
                state.analysis["current_stage"] = state.current_stage.value
                return state
        self.vitals_agent.process(state, vitals_payload=vitals_payload)
        self._log_event(state, agent=self.vitals_agent.name, action="evaluate_vitals")
        self.risk_agent.process(state)
        self._log_event(state, agent=self.risk_agent.name, action="analyze_risk")
        state.set_stage(WorkflowStage.DECISION)
        self.policy_engine.evaluate(state)
        state.replay_branch_id = state.policy_decision or "main"
        self._log_event(state, agent="policy-engine", action="evaluate_policy", decision=state.policy_decision)
        if state.policy_decision == "emergency_escalation":
            self.emergency_escalation_agent.process(state)
            self._log_event(state, agent=self.emergency_escalation_agent.name, action="create_emergency_escalation", decision=state.policy_decision)
        elif state.policy_decision != "emergency_escalation":
            try:
                self.scheduling_agent.process(state)
            finally:
                for telemetry in state.tool_telemetry:
                    record_tool_execution(telemetry)
            self._log_event(state, agent=self.scheduling_agent.name, action="plan_schedule", decision=state.policy_decision)
        if state.policy_decision == "emergency_escalation":
            state.analysis["available_dates"] = []
            state.analysis["doctor_matches"] = state.analysis.get("doctor_matches", [])
            state.analysis["next_slot"] = "Emergency escalation active"
            state.analysis["booking_mode"] = "emergency"
            state.analysis["policy_decision"] = state.policy_decision
            state.analysis["reasoning_trace"] = state.reasoning_trace
            state.analysis["tool_trace"] = []
            state.analysis["tool_telemetry"] = state.tool_telemetry
        self.communication_agent.process(state, awaiting_age=awaiting_age)
        self._log_event(state, agent=self.communication_agent.name, action="prepare_response", decision=state.policy_decision)
        return state
