from __future__ import annotations

from ..appointments import (
    fetch_active_risk_model,
    fetch_active_threshold_profile,
    fetch_candidate_risk_model,
    fetch_candidate_threshold_profile,
    persist_feature_snapshot,
    persist_risk_prediction,
)
from ..clinical_questionnaires import format_questionnaire_context
from ..clinical_runtime import build_clinical_summary, build_risk_explanation
from ..ml_governance import shadow_score_analysis
from ..pydantic_compat import model_dump
from ..workflow import CaseWorkflowState, WorkflowStage


class RiskAssessmentAgent:
    name = "risk-agent"

    def process(self, state: CaseWorkflowState) -> CaseWorkflowState:
        state.set_stage(WorkflowStage.TRIAGE)
        symptoms = str(state.intake_data.get("symptoms", "")).strip()
        questionnaire_payload = state.intake_data.get("clinical_questionnaire")
        questionnaire_context = format_questionnaire_context(questionnaire_payload if isinstance(questionnaire_payload, dict) else None)
        analysis_symptoms = f"{symptoms}\n{questionnaire_context}".strip() if questionnaire_context else symptoms
        patient_age = state.intake_data.get("patient_age")
        medical_history = str(state.intake_data.get("medical_history", "")).strip()
        active_model = fetch_active_risk_model()
        candidate_model = fetch_candidate_risk_model()
        active_threshold = fetch_active_threshold_profile()
        candidate_threshold = fetch_candidate_threshold_profile()
        analysis, feature_snapshot, active_prediction, candidate_prediction, comparison = shadow_score_analysis(
            workflow_id=state.conversation_id,
            patient_id=state.patient_id,
            conversation_id=state.conversation_id,
            symptoms=analysis_symptoms,
            patient_age=patient_age,
            medical_history=medical_history,
            known_context=state.known_context,
            active_model=dict(active_model),
            candidate_model=dict(candidate_model) if candidate_model else None,
            active_threshold=active_threshold,
            candidate_threshold=candidate_threshold,
        )
        feature_snapshot = persist_feature_snapshot(feature_snapshot)
        active_prediction.feature_snapshot_id = int(feature_snapshot.id or 0)
        persisted_active_prediction = persist_risk_prediction(active_prediction)
        persisted_candidate_prediction = None
        if candidate_prediction is not None:
            candidate_prediction.feature_snapshot_id = int(feature_snapshot.id or 0)
            persisted_candidate_prediction = persist_risk_prediction(candidate_prediction)
        analysis["symptoms"] = symptoms
        analysis["clinical_questionnaire"] = questionnaire_payload or {}
        analysis["clinical_questionnaire_summary"] = questionnaire_context
        analysis["vitals"] = state.intake_data.get("vitals") or {}
        analysis["known_context"] = state.known_context
        risk_explanation = build_risk_explanation(
            analysis=analysis,
            questionnaire_payload=questionnaire_payload if isinstance(questionnaire_payload, dict) else None,
            vitals_payload=state.intake_data.get("vitals") if isinstance(state.intake_data.get("vitals"), dict) else None,
        )
        analysis["risk_explanation"] = risk_explanation
        analysis["risk_score"] = risk_explanation["risk_score"]
        analysis["clinical_summary"] = build_clinical_summary(analysis)
        if risk_explanation["risk_level"] == "EMERGENCY":
            analysis["urgency"] = "Emergency"
            analysis["severity"] = "Emergency"
            analysis["requires_review"] = True
            analysis["recommended_action"] = "Immediate medical evaluation recommended"
        elif risk_explanation["risk_level"] == "URGENT" and analysis.get("urgency") == "Low":
            analysis["urgency"] = "High"
            analysis["requires_review"] = True
        analysis["ml_governance"]["feature_snapshot"] = model_dump(feature_snapshot)
        analysis["ml_governance"]["active_prediction"] = model_dump(persisted_active_prediction)
        analysis["ml_governance"]["candidate_prediction"] = model_dump(persisted_candidate_prediction) if persisted_candidate_prediction else None
        analysis["ml_governance"]["shadow_comparison"] = model_dump(comparison) if comparison else None
        analysis["ml_event_payload"] = {
            "feature_snapshot_hash": persisted_active_prediction.feature_snapshot_hash,
            "model_input_hash": persisted_active_prediction.model_input_hash,
            "threshold_profile_id": persisted_active_prediction.threshold_profile_id,
            "model_key": persisted_active_prediction.model_key,
            "model_version": persisted_active_prediction.model_version,
            "feature_version": persisted_active_prediction.feature_version,
            "raw_score": persisted_active_prediction.raw_score,
            "calibrated_score": persisted_active_prediction.calibrated_score,
            "risk_band": persisted_active_prediction.risk_band,
            "top_features": persisted_active_prediction.top_features_json,
            "shadow_comparison": model_dump(comparison) if comparison else None,
        }
        state.analysis = analysis
        state.ml_governance = dict(analysis.get("ml_governance", {}))
        state.risk_level = str(analysis["urgency"]).lower()
        state.confidence = float(analysis["confidence"])
        state.human_review_required = bool(analysis["requires_review"])
        state.next_action = "evaluate_policy"
        state.assigned_agent = "policy-engine"
        state.record(
            self.name,
            f"triage complete with urgency={analysis['urgency']} confidence={analysis['confidence']}",
        )
        return state
