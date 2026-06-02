from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from .contracts import (
    DriftDetectionSummary,
    DriftMetricSnapshot,
    FeatureSnapshotContract,
    ModelGovernanceSummary,
    RiskPredictionContract,
    ShadowPredictionComparison,
    ThresholdProfileContract,
)
from .ml import analyze_symptoms, detect_history_flags, extract_symptoms
from .pydantic_compat import model_dump


DEFAULT_MODEL_FAMILY = "sklearn-logreg"
DEFAULT_FEATURE_VERSION = "ml-v2-structured-v1"


def canonical_json_payload(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def hash_payload(payload: Any) -> str:
    return hashlib.sha256(canonical_json_payload(payload).encode("utf-8")).hexdigest()


def age_bucket(patient_age: int | None) -> str:
    if patient_age is None:
        return "unknown"
    if patient_age < 18:
        return "under_18"
    if patient_age < 40:
        return "18_39"
    if patient_age < 60:
        return "40_59"
    if patient_age < 75:
        return "60_74"
    return "75_plus"


def build_feature_snapshot(
    *,
    workflow_id: str,
    patient_id: str | None,
    conversation_id: str,
    symptom_text: str,
    patient_age: int | None,
    medical_history: str,
    known_context: dict[str, Any] | None,
    temporal_features: dict[str, Any] | None = None,
) -> FeatureSnapshotContract:
    extracted_symptoms = extract_symptoms(symptom_text)
    history_flags = detect_history_flags(medical_history)
    structured_features = {
        "patient_age": patient_age,
        "age_bucket": age_bucket(patient_age),
        "history_flags": sorted(history_flags),
        "history_flag_count": len(history_flags),
        "known_context_loaded": bool((known_context or {}).get("profile_loaded")),
        "known_age_loaded": bool((known_context or {}).get("used_age")),
        "known_history_loaded": bool((known_context or {}).get("history_loaded")),
        "extracted_symptoms": extracted_symptoms,
        "symptom_count": len(extracted_symptoms),
        "medical_history_present": bool(medical_history.strip()),
    }
    temporal = {
        "visit_count_30d": 0,
        "visit_count_90d": 0,
        "prior_emergency_count": 0,
        "prior_review_count": 0,
        "days_since_last_visit": None,
    }
    temporal.update(temporal_features or {})
    text_features_hash = hash_payload({"symptom_text": symptom_text.strip().lower(), "tokens": extracted_symptoms})
    feature_payload = {
        "workflow_id": workflow_id,
        "patient_id": patient_id,
        "conversation_id": conversation_id,
        "model_family": DEFAULT_MODEL_FAMILY,
        "feature_version": DEFAULT_FEATURE_VERSION,
        "symptom_text": symptom_text.strip(),
        "structured_features_json": structured_features,
        "temporal_features_json": temporal,
        "text_features_hash": text_features_hash,
    }
    feature_snapshot_hash = hash_payload(feature_payload)
    model_input_hash = hash_payload(
        {
            "model_family": DEFAULT_MODEL_FAMILY,
            "feature_version": DEFAULT_FEATURE_VERSION,
            "feature_snapshot_hash": feature_snapshot_hash,
            "symptom_text": symptom_text.strip(),
            "structured_features_json": structured_features,
            "temporal_features_json": temporal,
        }
    )
    return FeatureSnapshotContract(
        workflow_id=workflow_id,
        patient_id=patient_id,
        conversation_id=conversation_id,
        model_family=DEFAULT_MODEL_FAMILY,
        feature_version=DEFAULT_FEATURE_VERSION,
        feature_snapshot_hash=feature_snapshot_hash,
        model_input_hash=model_input_hash,
        created_at=dt.datetime.now().isoformat(timespec="seconds"),
        symptom_text=symptom_text.strip(),
        structured_features_json=structured_features,
        temporal_features_json=temporal,
        text_features_hash=text_features_hash,
        label_status="unlabeled",
        label_source="",
        label_updated_at=None,
    )


def recompute_snapshot_hashes(snapshot: FeatureSnapshotContract) -> tuple[str, str]:
    feature_payload = {
        "workflow_id": snapshot.workflow_id,
        "patient_id": snapshot.patient_id,
        "conversation_id": snapshot.conversation_id,
        "model_family": snapshot.model_family,
        "feature_version": snapshot.feature_version,
        "symptom_text": snapshot.symptom_text.strip(),
        "structured_features_json": snapshot.structured_features_json,
        "temporal_features_json": snapshot.temporal_features_json,
        "text_features_hash": snapshot.text_features_hash,
    }
    feature_snapshot_hash = hash_payload(feature_payload)
    model_input_hash = hash_payload(
        {
            "model_family": snapshot.model_family,
            "feature_version": snapshot.feature_version,
            "feature_snapshot_hash": feature_snapshot_hash,
            "symptom_text": snapshot.symptom_text.strip(),
            "structured_features_json": snapshot.structured_features_json,
            "temporal_features_json": snapshot.temporal_features_json,
        }
    )
    return feature_snapshot_hash, model_input_hash


def apply_thresholds(
    *,
    raw_score: float,
    calibrated_score: float,
    severity: str,
    urgency: str,
    threshold_profile: ThresholdProfileContract,
) -> tuple[str, bool, str]:
    thresholds = threshold_profile.thresholds_json
    if severity.lower() == "emergency" or urgency.lower() == "emergency":
        risk_band = "emergency"
    elif calibrated_score >= float(thresholds.get("emergency", 0.9)):
        risk_band = "emergency"
    elif severity.lower() == "high" or urgency.lower() == "high" or calibrated_score >= float(thresholds.get("high", 0.72)):
        risk_band = "high"
    elif calibrated_score >= float(thresholds.get("medium", 0.45)):
        risk_band = "medium"
    else:
        risk_band = "low"
    requires_review = urgency.lower() in {"high", "emergency"} or (calibrated_score * 100.0) < float(thresholds.get("review_confidence_lt", 75.0))
    threshold_trigger = "score"
    if severity.lower() == "emergency" or urgency.lower() == "emergency":
        threshold_trigger = "clinical_override"
    elif severity.lower() == "high" or urgency.lower() == "high":
        threshold_trigger = "high_risk_floor"
    elif calibrated_score >= float(thresholds.get("emergency", 0.9)):
        threshold_trigger = "emergency_threshold"
    elif calibrated_score >= float(thresholds.get("high", 0.72)):
        threshold_trigger = "high_threshold"
    elif calibrated_score >= float(thresholds.get("medium", 0.45)):
        threshold_trigger = "medium_threshold"
    return risk_band, requires_review, threshold_trigger


def build_top_features(analysis: dict[str, Any], feature_snapshot: FeatureSnapshotContract) -> list[dict[str, Any]]:
    factors: list[dict[str, Any]] = []
    patient_age = feature_snapshot.structured_features_json.get("patient_age")
    if patient_age is not None:
        factors.append({"feature": "patient_age", "value": patient_age, "contribution": round(min(float(patient_age) / 100.0, 1.0), 3)})
    history_flags = feature_snapshot.structured_features_json.get("history_flags", [])
    if history_flags:
        factors.append({"feature": "history_flags", "value": history_flags[:3], "contribution": round(min(len(history_flags) * 0.18, 0.9), 3)})
    extracted = analysis.get("extracted_symptoms", [])
    if extracted:
        factors.append({"feature": "extracted_symptoms", "value": extracted[:4], "contribution": round(min(len(extracted) * 0.12, 0.7), 3)})
    factors.append({"feature": "severity", "value": analysis.get("severity", "Low"), "contribution": 1.0 if str(analysis.get("severity", "")).lower() == "emergency" else 0.45})
    return factors[:4]


def build_prediction_contract(
    *,
    workflow_id: str,
    feature_snapshot: FeatureSnapshotContract,
    model_registry_id: int,
    model_key: str,
    model_version: str,
    threshold_profile: ThresholdProfileContract,
    analysis: dict[str, Any],
    is_shadow_prediction: bool,
    active_model_key: str,
    candidate_model_key: str,
) -> RiskPredictionContract:
    raw_score = round(min(max(float(analysis.get("priority_score", 0.0)) / 100.0, 0.0), 1.0), 4)
    calibrated_score = round(min(max(((raw_score * 0.6) + ((float(analysis.get("confidence", 0.0)) / 100.0) * 0.4)), 0.0), 1.0), 4)
    risk_band, requires_review, threshold_trigger = apply_thresholds(
        raw_score=raw_score,
        calibrated_score=calibrated_score,
        severity=str(analysis.get("severity", "Low")),
        urgency=str(analysis.get("urgency", "Low")),
        threshold_profile=threshold_profile,
    )
    top_features = build_top_features(analysis, feature_snapshot)
    explanations = {
        "threshold_trigger": threshold_trigger,
        "confidence_pct": float(analysis.get("confidence", 0.0)),
        "priority_score": float(analysis.get("priority_score", 0.0)),
        "queue_state": str(analysis.get("queue_state", "")),
    }
    return RiskPredictionContract(
        workflow_id=workflow_id,
        feature_snapshot_id=int(feature_snapshot.id or 0),
        model_registry_id=model_registry_id,
        created_at=dt.datetime.now().isoformat(timespec="seconds"),
        raw_score=raw_score,
        calibrated_score=calibrated_score,
        risk_band=risk_band,
        predicted_specialty=str(analysis.get("specialty", "General")),
        predicted_urgency=str(analysis.get("urgency", "Low")),
        predicted_severity=str(analysis.get("severity", "Low")),
        requires_review=requires_review,
        threshold_profile_id=int(threshold_profile.id or 0),
        feature_snapshot_hash=feature_snapshot.feature_snapshot_hash,
        model_input_hash=feature_snapshot.model_input_hash,
        model_key=model_key,
        model_version=model_version,
        feature_version=feature_snapshot.feature_version,
        active_model_key=active_model_key,
        candidate_model_key=candidate_model_key,
        explanations_json=explanations,
        top_features_json=top_features,
        is_shadow_prediction=is_shadow_prediction,
    )


def build_shadow_comparison(
    workflow_id: str,
    active_prediction: RiskPredictionContract,
    candidate_prediction: RiskPredictionContract,
) -> ShadowPredictionComparison:
    confidence_delta = round(candidate_prediction.calibrated_score - active_prediction.calibrated_score, 4)
    risk_band_delta = f"{active_prediction.risk_band} -> {candidate_prediction.risk_band}"
    threshold_trigger_delta = (
        f"{active_prediction.explanations_json.get('threshold_trigger', 'unknown')} -> "
        f"{candidate_prediction.explanations_json.get('threshold_trigger', 'unknown')}"
    )
    review_delta = bool(active_prediction.requires_review != candidate_prediction.requires_review)
    escalation_delta = (
        active_prediction.risk_band in {"high", "emergency"}
        and candidate_prediction.risk_band not in {"high", "emergency"}
    ) or (
        candidate_prediction.risk_band in {"high", "emergency"}
        and active_prediction.risk_band not in {"high", "emergency"}
    )
    if not any(
        [
            confidence_delta,
            active_prediction.risk_band != candidate_prediction.risk_band,
            active_prediction.predicted_specialty != candidate_prediction.predicted_specialty,
            active_prediction.predicted_urgency != candidate_prediction.predicted_urgency,
            active_prediction.predicted_severity != candidate_prediction.predicted_severity,
            review_delta,
        ]
    ):
        summary = "Active and candidate model predictions remained aligned under deterministic replay-safe scoring."
        policy_impact = "none"
    else:
        summary = "Candidate shadow prediction diverged from the active model under the same immutable feature snapshot."
        policy_impact = "review_delta" if review_delta else "band_delta"
    return ShadowPredictionComparison(
        workflow_id=workflow_id,
        active_model_key=active_prediction.model_key,
        candidate_model_key=candidate_prediction.model_key,
        active_prediction=active_prediction,
        candidate_prediction=candidate_prediction,
        divergence_summary=summary,
        confidence_delta=confidence_delta,
        risk_band_delta=risk_band_delta,
        threshold_trigger_delta=threshold_trigger_delta,
        review_recommendation_delta=review_delta,
        policy_impact_delta=policy_impact,
        escalation_delta=escalation_delta,
        replay_safe_explanation_payload={
            "active": {
                "feature_snapshot_hash": active_prediction.feature_snapshot_hash,
                "model_input_hash": active_prediction.model_input_hash,
                "threshold_profile_id": active_prediction.threshold_profile_id,
                "top_features": active_prediction.top_features_json,
            },
            "candidate": {
                "feature_snapshot_hash": candidate_prediction.feature_snapshot_hash,
                "model_input_hash": candidate_prediction.model_input_hash,
                "threshold_profile_id": candidate_prediction.threshold_profile_id,
                "top_features": candidate_prediction.top_features_json,
            },
        },
    )


def build_governance_summary(
    *,
    active_model_key: str,
    candidate_model_key: str,
    active_threshold_profile_id: int | None,
    candidate_threshold_profile_id: int | None,
    latest_feature_snapshot_hash: str,
    latest_model_input_hash: str,
    shadow_prediction_count: int,
    divergent_shadow_predictions: int,
    latest_shadow_comparison: ShadowPredictionComparison | None,
    latest_drift_summary: DriftDetectionSummary | None,
    latest_evaluation_run_id: int | None = None,
    latest_evaluation_status: str = "",
    promotion_readiness: str = "",
) -> ModelGovernanceSummary:
    return ModelGovernanceSummary(
        active_model_key=active_model_key,
        candidate_model_key=candidate_model_key,
        active_threshold_profile_id=active_threshold_profile_id,
        candidate_threshold_profile_id=candidate_threshold_profile_id,
        latest_feature_snapshot_hash=latest_feature_snapshot_hash,
        latest_model_input_hash=latest_model_input_hash,
        shadow_prediction_count=shadow_prediction_count,
        divergent_shadow_predictions=divergent_shadow_predictions,
        latest_shadow_comparison=latest_shadow_comparison,
        latest_drift_summary=latest_drift_summary,
        latest_evaluation_run_id=latest_evaluation_run_id,
        latest_evaluation_status=latest_evaluation_status,
        promotion_readiness=promotion_readiness,
    )


def build_drift_summary(
    *,
    active_model_key: str,
    candidate_model_key: str,
    rows: list[dict[str, Any]],
) -> DriftDetectionSummary:
    if not rows:
        return DriftDetectionSummary(active_model_key=active_model_key, candidate_model_key=candidate_model_key, generated_at=dt.datetime.now().isoformat(timespec="seconds"), metrics=[])
    score_deltas = [abs(float(row["candidate_calibrated_score"]) - float(row["active_calibrated_score"])) for row in rows]
    specialty_deltas = [1.0 for row in rows if row["candidate_predicted_specialty"] != row["active_predicted_specialty"]]
    review_rate_active = sum(1 for row in rows if int(row["active_requires_review"]) == 1) / max(len(rows), 1)
    review_rate_candidate = sum(1 for row in rows if int(row["candidate_requires_review"]) == 1) / max(len(rows), 1)
    emergency_policy_workflows = [row for row in rows if str(row.get("policy_decision", "")) == "emergency_escalation"]
    candidate_missed_emergency = [
        row
        for row in emergency_policy_workflows
        if str(row["candidate_risk_band"]) not in {"high", "emergency"}
    ]
    active_calibration_gap = [abs(float(row["active_raw_score"]) - float(row["active_calibrated_score"])) for row in rows]
    candidate_calibration_gap = [abs(float(row["candidate_raw_score"]) - float(row["candidate_calibrated_score"])) for row in rows]
    metrics = [
        DriftMetricSnapshot(
            metric_key="score_distribution_drift",
            value=round((sum(score_deltas) / max(len(score_deltas), 1)) * 100.0, 2),
            baseline=0.0,
            delta=round((sum(score_deltas) / max(len(score_deltas), 1)) * 100.0, 2),
            severity="watch" if sum(score_deltas) > 0 else "nominal",
            detail="Average absolute calibrated-score delta between active and candidate predictions.",
        ),
        DriftMetricSnapshot(
            metric_key="specialty_distribution_drift",
            value=round((len(specialty_deltas) / max(len(rows), 1)) * 100.0, 2),
            baseline=0.0,
            delta=round((len(specialty_deltas) / max(len(rows), 1)) * 100.0, 2),
            severity="watch" if specialty_deltas else "nominal",
            detail="Share of workflows where candidate specialty routing differs from the active model.",
        ),
        DriftMetricSnapshot(
            metric_key="emergency_false_negative_drift",
            value=round((len(candidate_missed_emergency) / max(len(emergency_policy_workflows), 1)) * 100.0, 2) if emergency_policy_workflows else 0.0,
            baseline=0.0,
            delta=round((len(candidate_missed_emergency) / max(len(emergency_policy_workflows), 1)) * 100.0, 2) if emergency_policy_workflows else 0.0,
            severity="critical" if candidate_missed_emergency else "nominal",
            detail="Candidate downgrade rate on workflows that ended in emergency policy escalation.",
        ),
        DriftMetricSnapshot(
            metric_key="review_rate_drift",
            value=round(review_rate_candidate * 100.0, 2),
            baseline=round(review_rate_active * 100.0, 2),
            delta=round(abs(review_rate_candidate - review_rate_active) * 100.0, 2),
            severity="watch" if review_rate_candidate != review_rate_active else "nominal",
            detail="Deterministic delta between candidate and active review recommendation rates.",
        ),
        DriftMetricSnapshot(
            metric_key="calibration_error_drift",
            value=round(sum(candidate_calibration_gap) / max(len(candidate_calibration_gap), 1) * 100.0, 2),
            baseline=round(sum(active_calibration_gap) / max(len(active_calibration_gap), 1) * 100.0, 2),
            delta=round(abs((sum(candidate_calibration_gap) / max(len(candidate_calibration_gap), 1)) - (sum(active_calibration_gap) / max(len(active_calibration_gap), 1))) * 100.0, 2),
            severity="watch" if candidate_calibration_gap != active_calibration_gap else "nominal",
            detail="Average absolute raw-to-calibrated score gap for candidate versus active predictions.",
        ),
    ]
    return DriftDetectionSummary(
        active_model_key=active_model_key,
        candidate_model_key=candidate_model_key,
        generated_at=dt.datetime.now().isoformat(timespec="seconds"),
        metrics=metrics,
    )


def shadow_score_analysis(
    *,
    workflow_id: str,
    patient_id: str | None,
    conversation_id: str,
    symptoms: str,
    patient_age: int | None,
    medical_history: str,
    known_context: dict[str, Any] | None,
    active_model: dict[str, Any],
    candidate_model: dict[str, Any] | None,
    active_threshold: ThresholdProfileContract,
    candidate_threshold: ThresholdProfileContract | None,
) -> tuple[dict[str, Any], FeatureSnapshotContract, RiskPredictionContract, RiskPredictionContract | None, ShadowPredictionComparison | None]:
    analysis = analyze_symptoms(symptoms, patient_age=patient_age, medical_history=medical_history)
    feature_snapshot = build_feature_snapshot(
        workflow_id=workflow_id,
        patient_id=patient_id,
        conversation_id=conversation_id,
        symptom_text=symptoms,
        patient_age=patient_age,
        medical_history=medical_history,
        known_context=known_context,
    )
    active_prediction = build_prediction_contract(
        workflow_id=workflow_id,
        feature_snapshot=feature_snapshot,
        model_registry_id=int(active_model["id"]),
        model_key=str(active_model["model_key"]),
        model_version=str(active_model["training_dataset_version"]),
        threshold_profile=active_threshold,
        analysis=analysis,
        is_shadow_prediction=False,
        active_model_key=str(active_model["model_key"]),
        candidate_model_key=str(candidate_model["model_key"]) if candidate_model else "",
    )
    candidate_prediction = None
    comparison = None
    if candidate_model and candidate_threshold:
        candidate_prediction = build_prediction_contract(
            workflow_id=workflow_id,
            feature_snapshot=feature_snapshot,
            model_registry_id=int(candidate_model["id"]),
            model_key=str(candidate_model["model_key"]),
            model_version=str(candidate_model["training_dataset_version"]),
            threshold_profile=candidate_threshold,
            analysis=analysis,
            is_shadow_prediction=True,
            active_model_key=str(active_model["model_key"]),
            candidate_model_key=str(candidate_model["model_key"]),
        )
        comparison = build_shadow_comparison(workflow_id, active_prediction, candidate_prediction)

    active_payload = model_dump(active_prediction)
    active_payload["top_features"] = active_prediction.top_features_json
    active_payload["threshold_trigger"] = active_prediction.explanations_json.get("threshold_trigger", "score")
    analysis["ml_governance"] = {
        "feature_snapshot": model_dump(feature_snapshot),
        "active_prediction": active_payload,
        "candidate_prediction": model_dump(candidate_prediction) if candidate_prediction else None,
        "shadow_comparison": model_dump(comparison) if comparison else None,
    }
    analysis["risk_band"] = active_prediction.risk_band
    analysis["requires_review"] = active_prediction.requires_review
    analysis["confidence"] = round(active_prediction.calibrated_score * 100.0, 1)
    return analysis, feature_snapshot, active_prediction, candidate_prediction, comparison
