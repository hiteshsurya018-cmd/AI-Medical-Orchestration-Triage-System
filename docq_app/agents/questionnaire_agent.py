from __future__ import annotations

from typing import Any

from ..clinical_questionnaires import format_questionnaire_context, next_question, select_questionnaire
from ..workflow import CaseWorkflowState, WorkflowStage


class QuestionnaireAgent:
    name = "questionnaire-agent"

    def prepare(self, state: CaseWorkflowState) -> CaseWorkflowState:
        state.set_stage(WorkflowStage.FOLLOWUP)
        questionnaire = select_questionnaire(str(state.intake_data.get("symptoms", "")))
        if questionnaire is None:
            state.record(self.name, "no dynamic questionnaire matched")
            return state
        question = next_question(questionnaire)
        if question is None:
            state.record(self.name, "questionnaire already complete")
            return state
        state.missing_fields = ["clinical_questionnaire"]
        state.follow_up_questions = [question["text"]]
        state.next_action = "collect_clinical_questionnaire"
        state.assigned_agent = self.name
        state.known_context["questionnaire"] = {
            "id": questionnaire["id"],
            "label": questionnaire["label"],
            "questions": questionnaire["questions"],
            "current_question_id": question["id"],
            "answers": {},
        }
        state.record(self.name, f"requested {question['id']} for {questionnaire['id']}")
        return state

    def hydrate_answers(self, state: CaseWorkflowState, questionnaire_payload: dict[str, Any] | None) -> CaseWorkflowState:
        if not questionnaire_payload:
            return state
        context = format_questionnaire_context(questionnaire_payload)
        state.intake_data["clinical_questionnaire"] = questionnaire_payload
        state.known_context["questionnaire_completed"] = True
        state.known_context["questionnaire"] = questionnaire_payload
        if context:
            state.known_context["questionnaire_summary"] = context
        state.record(self.name, "loaded dynamic questionnaire answers")
        return state
