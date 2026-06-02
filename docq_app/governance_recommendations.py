from __future__ import annotations

import datetime as dt
from typing import Any

from .contracts import GovernanceIncidentCorrelation, GovernanceRecommendation, ModelEvaluationRun
from .pydantic_compat import model_dump


def synthesize_governance_recommendations(
    *,
    evaluation_run: ModelEvaluationRun,
    drift_snapshot: dict[str, Any] | None,
    incident_correlation: GovernanceIncidentCorrelation | None,
) -> list[GovernanceRecommendation]:
    recommendations: list[GovernanceRecommendation] = []
    summary = evaluation_run.summary_json
    now = dt.datetime.now().isoformat(timespec="seconds")
    base_evidence = {
        "evaluation_run_id": evaluation_run.id,
        "evaluation_checksum": summary.evaluation_checksum,
        "incident_correlation": model_dump(incident_correlation) if incident_correlation else None,
        "drift_snapshot": drift_snapshot or {},
    }
    gate_passed = bool(evaluation_run.promotion_gate_result and evaluation_run.promotion_gate_result.passed)
    if gate_passed and summary.replay_integrity_passed and summary.false_negative_delta <= 0.0:
        recommendations.append(
            GovernanceRecommendation(
                recommendation_key=f"promotion-{evaluation_run.evaluation_run_key}",
                recommendation_type="promotion",
                source_evaluation_run_id=evaluation_run.id,
                candidate_model_registry_id=evaluation_run.candidate_model_registry_id,
                threshold_profile_id=evaluation_run.candidate_threshold_profile_id,
                recommendation_status="pending",
                recommendation_reason="Candidate model satisfied promotion gate criteria under replay-safe offline evaluation.",
                confidence_score=max(0.0, 100.0 - summary.review_rate_delta - abs(summary.calibration_delta)),
                created_at=now,
                supporting_evidence_json={**base_evidence, "gate_result": evaluation_run.promotion_gate_result.model_dump() if evaluation_run.promotion_gate_result else {}},
            )
        )
    if summary.false_negative_delta > 0.0 or not summary.replay_integrity_passed:
        recommendations.append(
            GovernanceRecommendation(
                recommendation_key=f"rollback-{evaluation_run.evaluation_run_key}",
                recommendation_type="rollback",
                source_evaluation_run_id=evaluation_run.id,
                candidate_model_registry_id=evaluation_run.candidate_model_registry_id,
                threshold_profile_id=evaluation_run.candidate_threshold_profile_id,
                recommendation_status="pending",
                recommendation_reason="Candidate model increased emergency false-negative or replay-integrity risk.",
                confidence_score=min(100.0, 70.0 + summary.false_negative_delta),
                created_at=now,
                supporting_evidence_json=base_evidence,
            )
        )
    if summary.review_rate_delta > 10.0 or abs(summary.calibration_delta) > 5.0:
        recommendations.append(
            GovernanceRecommendation(
                recommendation_key=f"threshold-adjustment-{evaluation_run.evaluation_run_key}",
                recommendation_type="threshold_adjustment",
                source_evaluation_run_id=evaluation_run.id,
                candidate_model_registry_id=evaluation_run.candidate_model_registry_id,
                threshold_profile_id=evaluation_run.candidate_threshold_profile_id,
                recommendation_status="pending",
                recommendation_reason="Threshold profile drifted review or calibration deltas beyond preferred operational bounds.",
                confidence_score=min(100.0, 55.0 + abs(summary.calibration_delta)),
                created_at=now,
                supporting_evidence_json=base_evidence,
            )
        )
    if drift_snapshot:
        metrics = drift_snapshot.get("metrics", [])
        triggered_metrics = [item["metric_key"] for item in metrics if item.get("severity") in {"watch", "elevated", "critical"}]
        if triggered_metrics:
            recommendations.append(
                GovernanceRecommendation(
                    recommendation_key=f"drift-alert-{evaluation_run.evaluation_run_key}",
                    recommendation_type="drift_alert",
                    source_evaluation_run_id=evaluation_run.id,
                    candidate_model_registry_id=evaluation_run.candidate_model_registry_id,
                    threshold_profile_id=evaluation_run.candidate_threshold_profile_id,
                    recommendation_status="pending",
                    recommendation_reason="Deterministic drift metrics exceeded governance watch thresholds.",
                    confidence_score=72.0,
                    created_at=now,
                    supporting_evidence_json={**base_evidence, "triggered_metrics": triggered_metrics},
                )
            )
    if not recommendations:
        recommendations.append(
            GovernanceRecommendation(
                recommendation_key=f"reevaluation-{evaluation_run.evaluation_run_key}",
                recommendation_type="reevaluation",
                source_evaluation_run_id=evaluation_run.id,
                candidate_model_registry_id=evaluation_run.candidate_model_registry_id,
                threshold_profile_id=evaluation_run.candidate_threshold_profile_id,
                recommendation_status="pending",
                recommendation_reason="Evaluation completed without a promotion-safe conclusion; retain candidate in shadow and reevaluate later.",
                confidence_score=60.0,
                created_at=now,
                supporting_evidence_json=base_evidence,
            )
        )
    return recommendations
