from __future__ import annotations

import datetime as dt
from typing import Any

from .appointments import (
    build_model_evaluation_run_contract,
    build_drift_trigger_contract,
    build_governance_recommendation_contract,
    build_governance_state_snapshot,
    build_governance_timeline_event_contract,
    build_rollout_profile_contract,
    fetch_active_rollout_profile,
    fetch_drift_trigger_rules,
    fetch_governance_recommendations,
    fetch_governance_timeline,
    fetch_model_evaluation_runs,
    fetch_model_evaluation_run,
    fetch_rollout_profiles,
    persist_governance_recommendation,
    persist_governance_timeline_event,
    record_governance_event,
)
from .contracts import DriftTriggerResult, GovernanceIncidentCorrelation, GovernanceTimelineEvent, RolloutSimulationProfile
from .governance_recommendations import synthesize_governance_recommendations
from .model_evaluation import get_model_evaluation_drift, run_offline_model_evaluation
from .ml_governance import hash_payload
from .pydantic_compat import model_dump


def _metric_value(drift_summary: dict[str, Any], metric_key: str) -> tuple[float, str]:
    for item in drift_summary.get("metrics", []):
        if item.get("metric_key") == metric_key:
            return float(item.get("delta", item.get("value", 0.0))), str(item.get("severity", "nominal"))
    return 0.0, "nominal"


def evaluate_drift_triggers(drift_summary: dict[str, Any]) -> list[DriftTriggerResult]:
    checksum = hash_payload(drift_summary)
    results: list[DriftTriggerResult] = []
    for row in fetch_drift_trigger_rules(limit=50):
        observed, severity = _metric_value(drift_summary, str(row["drift_metric_type"]))
        triggered = observed > float(row["threshold_value"]) or (float(row["threshold_value"]) == 0.0 and observed > 0.0)
        results.append(
            build_drift_trigger_contract(
                row,
                triggered=triggered,
                source=severity,
                governance_checksum=checksum,
            )
        )
    return results


def simulate_rollout_profile(evaluation_run: dict[str, Any], rollout_profile: RolloutSimulationProfile) -> dict[str, Any]:
    summary = evaluation_run.get("summary_json", {})
    integrity_ok = bool(summary.get("replay_integrity_passed", False))
    stages = []
    for pct in rollout_profile.rollout_percentages_json:
        scale = float(pct) / 100.0
        stages.append(
            {
                "percentage": pct,
                "escalation_impact": round(float(summary.get("escalation_delta", 0.0)) * scale, 2),
                "review_rate_impact": round(float(summary.get("review_rate_delta", 0.0)) * scale, 2),
                "false_negative_risk": round(float(summary.get("false_negative_delta", 0.0)) * scale, 2),
                "drift_severity": "critical" if float(summary.get("false_negative_delta", 0.0)) > 0 else ("watch" if float(summary.get("review_rate_delta", 0.0)) > 0 else "nominal"),
                "policy_divergence": round(float(summary.get("divergence_count", 0)) * scale, 2),
                "incident_risk": round((float(summary.get("review_rate_delta", 0.0)) + max(float(summary.get("false_negative_delta", 0.0)), 0.0)) * scale, 2),
                "replay_integrity_confidence": 100.0 if integrity_ok else 0.0,
            }
        )
    governance_checksum = hash_payload({"evaluation_checksum": summary.get("evaluation_checksum", ""), "stages": stages})
    return {
        "rollout_profile_key": rollout_profile.rollout_profile_key,
        "governance_checksum": governance_checksum,
        "stages": stages,
    }


def _build_incident_correlation(trigger_results: list[DriftTriggerResult]) -> GovernanceIncidentCorrelation | None:
    triggered = [item for item in trigger_results if item.triggered]
    if not triggered:
        return None
    highest = triggered[0]
    severity = "critical" if highest.drift_metric_type == "emergency_false_negative_drift" else "watch"
    return GovernanceIncidentCorrelation(
        incident_correlation_id=f"governance-{highest.rule_key}",
        source=highest.drift_metric_type,
        severity=severity,
        detail=f"Deterministic governance trigger fired for {highest.drift_metric_type}.",
    )


def _build_governance_state(*, drift_summary: dict[str, Any], trigger_results: list[DriftTriggerResult]) -> dict[str, Any]:
    incident = _build_incident_correlation(trigger_results)
    active_recommendations = [build_governance_recommendation_contract(row) for row in fetch_governance_recommendations(limit=50)]
    rollout_profiles = [build_rollout_profile_contract(row) for row in fetch_rollout_profiles(limit=20)]
    timeline = [build_governance_timeline_event_contract(row) for row in fetch_governance_timeline(limit=50)]
    latest_runs = fetch_model_evaluation_runs(limit=1)
    latest_run = build_model_evaluation_run_contract(latest_runs[0]) if latest_runs else None
    rollout_simulation = None
    if latest_run and rollout_profiles:
        rollout_simulation = simulate_rollout_profile(model_dump(latest_run), rollout_profiles[0])
    governance_checksum = hash_payload(
        {
            "recommendations": [item.recommendation_key for item in active_recommendations],
            "triggers": [model_dump(item) for item in trigger_results],
            "timeline_count": len(timeline),
            "rollout_profile_keys": [item.rollout_profile_key for item in rollout_profiles],
        }
    )
    recommendation_confidence = round(sum(item.confidence_score for item in active_recommendations[:5]) / max(len(active_recommendations[:5]), 1), 2) if active_recommendations else 0.0
    rollout_risk_score = max((max((stage["incident_risk"] for stage in rollout_simulation["stages"]), default=0.0) if rollout_simulation else 0.0), 0.0)
    rollback_risk_score = max((item.confidence_score for item in active_recommendations if item.recommendation_type == "rollback"), default=0.0)
    snapshot = build_governance_state_snapshot(
        governance_checksum=governance_checksum,
        recommendation_confidence=recommendation_confidence,
        rollout_risk_score=rollout_risk_score,
        rollback_risk_score=rollback_risk_score,
        incident_correlation=model_dump(incident) if incident else None,
        active_recommendations=active_recommendations[:10],
        drift_triggers=trigger_results,
        rollout_profiles=rollout_profiles,
        timeline=timeline[:20],
    )
    return model_dump(snapshot)


def run_continuous_governance(*, refresh: bool = True) -> dict[str, Any]:
    latest_runs = fetch_model_evaluation_runs(limit=1)
    latest_run = build_model_evaluation_run_contract(latest_runs[0]) if latest_runs else None
    drift_summary = get_model_evaluation_drift(int(latest_run.id)) if latest_run and latest_run.id else {"metrics": []}
    trigger_results = evaluate_drift_triggers(drift_summary)
    triggered = [item for item in trigger_results if item.triggered]
    evaluation_run = None
    if refresh and triggered:
        evaluation_run = run_offline_model_evaluation("latest-25")
        record_governance_event(
            str(evaluation_run["evaluation_run_key"]),
            action="drift_triggered_evaluation",
            decision="launch_evaluation",
            payload={
                "evaluation_run_id": evaluation_run["id"],
                "drift_trigger_rule": triggered[0].rule_key,
                "governance_state": "triggered",
                "governance_checksum": triggered[0].governance_checksum,
            },
            confidence=80.0,
            reasons=[f"Triggered by {item.rule_key}" for item in triggered],
        )
    if evaluation_run is None and latest_run:
        evaluation_run = model_dump(latest_run)
    incident = _build_incident_correlation(trigger_results)
    recommendations = []
    rollout_simulation = None
    if refresh and evaluation_run:
        for item in synthesize_governance_recommendations(
            evaluation_run=build_model_evaluation_run_contract(fetch_model_evaluation_run(int(evaluation_run["id"]))),
            drift_snapshot=drift_summary,
            incident_correlation=incident,
        ):
            persisted = persist_governance_recommendation(item)
            recommendations.append(persisted)
            persist_governance_timeline_event(
                GovernanceTimelineEvent(
                    governance_entity_type="recommendation",
                    governance_entity_id=int(persisted.id or 0),
                    event_type="recommendation_created",
                    event_timestamp=dt.datetime.now().isoformat(timespec="seconds"),
                    related_model_key=evaluation_run.get("promotion_recommendation", ""),
                    related_threshold_profile_key="",
                    incident_correlation_id=incident.incident_correlation_id if incident else "",
                    payload_json=model_dump(persisted),
                )
            )
        rollout_row = fetch_active_rollout_profile()
        if rollout_row:
            rollout_profile = build_rollout_profile_contract(rollout_row)
            rollout_simulation = simulate_rollout_profile(evaluation_run, rollout_profile)
            persist_governance_timeline_event(
                GovernanceTimelineEvent(
                    governance_entity_type="rollout_profile",
                    governance_entity_id=int(rollout_profile.id or 0),
                    event_type="rollout_simulated",
                    event_timestamp=dt.datetime.now().isoformat(timespec="seconds"),
                    related_model_key="",
                    related_threshold_profile_key="",
                    incident_correlation_id=incident.incident_correlation_id if incident else "",
                    payload_json=rollout_simulation,
                )
            )
    return _build_governance_state(drift_summary=drift_summary, trigger_results=trigger_results)
