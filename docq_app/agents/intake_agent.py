from __future__ import annotations

import re

from ..workflow import CaseWorkflowState, WorkflowStage


class IntakeAgent:
    name = "intake-agent"

    def _extract_age_value(self, message: str) -> int | None:
        cleaned = (message or "").strip()
        if not cleaned:
            return None
        if cleaned.isdigit():
            value = int(cleaned)
            return value if 0 <= value <= 120 else None
        match = re.search(r"\b(\d{1,3})\b", cleaned)
        if not match:
            return None
        value = int(match.group(1))
        return value if 0 <= value <= 120 else None

    def process(self, state: CaseWorkflowState, *, awaiting_age: bool = False) -> CaseWorkflowState:
        state.set_stage(WorkflowStage.FOLLOWUP if awaiting_age else WorkflowStage.INTAKE)
        if awaiting_age:
            parsed_age = self._extract_age_value(state.raw_message)
            if parsed_age is None:
                state.missing_fields = ["patient_age"]
                state.follow_up_questions = ["I still need the patient's age to continue triage."]
                state.next_action = "collect_missing_info"
                state.assigned_agent = self.name
                state.record(self.name, "age value invalid or missing")
                return state
            state.intake_data["patient_age"] = parsed_age
            state.known_context["used_age"] = parsed_age
            state.next_action = "analyze_risk"
            state.assigned_agent = "risk-agent"
            state.record(self.name, "captured missing age from follow-up")
            return state

        symptoms = (state.raw_message or "").strip()
        state.intake_data["symptoms"] = symptoms
        if not state.known_context.get("used_age") and state.intake_data.get("patient_age") is None:
            state.missing_fields = ["patient_age"]
            state.follow_up_questions = ["May I know the patient's age?"]
            state.next_action = "collect_missing_info"
            state.assigned_agent = self.name
            state.record(self.name, "requested age before triage")
            return state

        state.next_action = "analyze_risk"
        state.assigned_agent = "risk-agent"
        state.record(self.name, "intake data ready for triage")
        return state
