from __future__ import annotations

import datetime as dt
import json
from typing import Any

from .appointments import (
    build_evaluation_drift_snapshot_contract,
    build_model_evaluation_result_contract,
    build_model_evaluation_run_contract,
    build_risk_prediction_contract,
    fetch_candidate_risk_model,
    fetch_candidate_threshold_profile,
    fetch_active_promotion_gate_profile,
    fetch_active_risk_model,
    fetch_active_threshold_profile,
    fetch_evaluation_drift_snapshot,
    fetch_latest_workflow_feature_snapshots,
    fetch_model_evaluation_results,
    fetch_model_evaluation_run,
    fetch_model_evaluation_runs,
    fetch_workflow_predictions,
    persist_evaluation_drift_snapshot,
    persist_model_evaluation_result,
    persist_model_evaluation_run,
    record_evaluation_event,
    update_model_evaluation_run,
)
from .contracts import (
    CandidatePolicyDelta,
    EvaluationDriftSnapshot,
    EvaluationSummary,
    ModelEvaluationResult,
    ModelEvaluationRun,
    ReplaySimulationResult,
    RiskPredictionContract,
    ThresholdSimulationResult,
)
from .ml import analyze_symptoms
from .ml_governance import build_prediction_contract, hash_payload, recompute_snapshot_hashes
from .policy_engine import PolicyEngine
from .promotion_gates import evaluate_promotion_gate
from .pydantic_compat import model_dump
from .runtime_diagnostics import verify_replay_integrity
from .workflow import CaseWorkflowState
from .appointments import build_workflow_replay
from .contracts import WorkflowReplay


def _workflow_limit_from_scope(scope: str) -> int:
    if scope.startswith("latest-"):
        try:
            return max(1, min(int(scope.split("-", 1)[1]), 200))
        except ValueError:
            return 25
    return 25


def _simulate_policy(prediction: RiskPredictionContract, feature_snapshot, history_flags: list[str]) -> str:
    state = CaseWorkflowState(
        conversation_id=feature_snapshot.conversation_id,
        raw_message=feature_snapshot.symptom_text,
        patient_id=feature_snapshot.patient_id,
    )
    state.intake_data["patient_age"] = feature_snapshot.structured_features_json.get("patient_age")
    state.known_context["history_loaded"] = bool(feature_snapshot.structured_features_json.get("known_history_loaded"))
    state.analysis = {
        "urgency": prediction.predicted_urgency,
        "severity": prediction.predicted_severity,
        "confidence": round(prediction.calibrated_score * 100.0, 1),
        "history_flags": history_flags,
    }
    PolicyEngine().evaluate(state)
    return state.policy_decision


def _select_latest_prediction(rows: list, *, is_shadow: bool) -> RiskPredictionContract | None:
    for row in reversed(rows):
        if bool(row["is_shadow_prediction"]) == is_shadow:
            return build_risk_prediction_contract(row)
    return None


def _build_divergence_summary(
    *,
    workflow_id: str,
    active_prediction: RiskPredictionContract,
    candidate_prediction: RiskPredictionContract,
    active_policy_path: str,
    candidate_policy_path: str,
) -> tuple[CandidatePolicyDelta, ThresholdSimulationResult, dict[str, Any]]:
    active_trigger = str(active_prediction.explanations_json.get("threshold_trigger", "score"))
    candidate_trigger = str(candidate_prediction.explanations_json.get("threshold_trigger", "score"))
    policy_delta = CandidatePolicyDelta(
        workflow_id=workflow_id,
        active_policy_path=active_policy_path,
        candidate_policy_path=candidate_policy_path,
        escalation_delta=(active_policy_path == "emergency_escalation") != (candidate_policy_path == "emergency_escalation"),
        review_delta=active_prediction.requires_review != candidate_prediction.requires_review,
        threshold_delta=f"{active_trigger} -> {candidate_trigger}",
        severity_delta=f"{active_prediction.predicted_severity} -> {candidate_prediction.predicted_severity}",
        specialty_delta=f"{active_prediction.predicted_specialty} -> {candidate_prediction.predicted_specialty}",
        calibration_delta=round(candidate_prediction.calibrated_score - active_prediction.calibrated_score, 4),
        false_negative_risk=(active_policy_path == "emergency_escalation" and candidate_policy_path != "emergency_escalation"),
    )
    threshold_simulation = ThresholdSimulationResult(
        workflow_id=workflow_id,
        active_threshold_profile_id=active_prediction.threshold_profile_id,
        candidate_threshold_profile_id=candidate_prediction.threshold_profile_id,
        threshold_trigger_delta=f"{active_trigger} -> {candidate_trigger}",
        escalation_sensitivity_delta=1.0 if policy_delta.escalation_delta else 0.0,
        review_sensitivity_delta=1.0 if policy_delta.review_delta else 0.0,
    )
    divergence = {
        "workflow_id": workflow_id,
        "policy_delta": model_dump(policy_delta),
        "threshold_simulation": model_dump(threshold_simulation),
        "active_prediction": {
            "risk_band": active_prediction.risk_band,
            "calibrated_score": active_prediction.calibrated_score,
            "specialty": active_prediction.predicted_specialty,
        },
        "candidate_prediction": {
            "risk_band": candidate_prediction.risk_band,
            "calibrated_score": candidate_prediction.calibrated_score,
            "specialty": candidate_prediction.predicted_specialty,
        },
    }
    return policy_delta, threshold_simulation, divergence


def _compute_evaluation_checksum(results: list[dict[str, Any]]) -> str:
    canonical = [
        {
            "workflow_id": item["workflow_id"],
            "feature_snapshot_id": item["feature_snapshot_id"],
            "replay_integrity_status": item["replay_integrity_status"],
            "active_policy_path": item["active_policy_path"],
            "candidate_policy_path": item["candidate_policy_path"],
            "threshold_delta": item["threshold_delta"],
            "severity_delta": item["severity_delta"],
            "specialty_delta": item["specialty_delta"],
            "calibration_delta": item["calibration_delta"],
            "false_negative_risk": item["false_negative_risk"],
        }
        for item in results
    ]
    return hash_payload(canonical)


def run_offline_model_evaluation(evaluation_scope: str = "latest-25") -> dict[str, Any]:
    active_model = fetch_active_risk_model()
    candidate_model = fetch_candidate_risk_model()
    active_threshold = fetch_active_threshold_profile()
    candidate_threshold = fetch_candidate_threshold_profile()
    gate_profile = fetch_active_promotion_gate_profile()
    limit = _workflow_limit_from_scope(evaluation_scope)
    snapshots = fetch_latest_workflow_feature_snapshots(limit=limit)
    run = persist_model_evaluation_run(
        ModelEvaluationRun(
            evaluation_run_key=f"eval-{dt.datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            candidate_model_registry_id=int(candidate_model["id"]) if candidate_model else int(active_model["id"]),
            active_model_registry_id=int(active_model["id"]),
            candidate_threshold_profile_id=int(candidate_threshold.id if candidate_threshold else active_threshold.id),
            active_threshold_profile_id=int(active_threshold.id),
            evaluation_scope=evaluation_scope,
            workflow_count=0,
            started_at=dt.datetime.now().isoformat(timespec="seconds"),
            status="running",
            replay_integrity_passed=True,
            evaluation_checksum="",
            summary_json=EvaluationSummary(evaluation_checksum=""),
            promotion_recommendation="hold",
            promotion_gate_result=None,
        )
    )
    record_evaluation_event(
        run.evaluation_run_key,
        action="start_evaluation",
        decision="running",
        payload={
            "evaluation_run_id": run.id,
            "candidate_model_key": str(candidate_model["model_key"]) if candidate_model else str(active_model["model_key"]),
            "threshold_profile_key": candidate_threshold.profile_key if candidate_threshold else active_threshold.profile_key,
            "integrity_status": "running",
        },
    )
    results: list[dict[str, Any]] = []
    integrity_passed = True
    for snapshot in snapshots:
        recomputed_feature_hash, recomputed_model_input_hash = recompute_snapshot_hashes(snapshot)
        prediction_rows = fetch_workflow_predictions(snapshot.workflow_id)
        persisted_active = _select_latest_prediction(prediction_rows, is_shadow=False)
        persisted_candidate = _select_latest_prediction(prediction_rows, is_shadow=True)
        if persisted_active is None or persisted_candidate is None:
            continue
        replay = WorkflowReplay(**build_workflow_replay(snapshot.workflow_id, limit=120))
        integrity = verify_replay_integrity(snapshot.workflow_id, replay, WorkflowReplay(**build_workflow_replay(snapshot.workflow_id, limit=120)))
        hash_integrity = (
            recomputed_feature_hash == snapshot.feature_snapshot_hash
            and recomputed_model_input_hash == snapshot.model_input_hash
            and integrity.feature_hash_consistency
            and integrity.model_input_consistency
        )
        integrity_status = "passed" if integrity.replay_match and hash_integrity else "invalidated"
        if integrity_status != "passed":
            integrity_passed = False
        history_flags = list(snapshot.structured_features_json.get("history_flags", []))
        medical_history = " ".join(history_flags)
        analysis = analyze_symptoms(
            snapshot.symptom_text,
            patient_age=snapshot.structured_features_json.get("patient_age"),
            medical_history=medical_history,
        )
        active_prediction = build_prediction_contract(
            workflow_id=snapshot.workflow_id,
            feature_snapshot=snapshot,
            model_registry_id=int(active_model["id"]),
            model_key=str(active_model["model_key"]),
            model_version=str(active_model["training_dataset_version"]),
            threshold_profile=active_threshold,
            analysis=analysis,
            is_shadow_prediction=False,
            active_model_key=str(active_model["model_key"]),
            candidate_model_key=str(candidate_model["model_key"]) if candidate_model else "",
        )
        candidate_prediction = build_prediction_contract(
            workflow_id=snapshot.workflow_id,
            feature_snapshot=snapshot,
            model_registry_id=int(candidate_model["id"]) if candidate_model else int(active_model["id"]),
            model_key=str(candidate_model["model_key"]) if candidate_model else str(active_model["model_key"]),
            model_version=str(candidate_model["training_dataset_version"]) if candidate_model else str(active_model["training_dataset_version"]),
            threshold_profile=candidate_threshold or active_threshold,
            analysis=analysis,
            is_shadow_prediction=True,
            active_model_key=str(active_model["model_key"]),
            candidate_model_key=str(candidate_model["model_key"]) if candidate_model else "",
        )
        active_policy_path = _simulate_policy(active_prediction, snapshot, history_flags)
        candidate_policy_path = _simulate_policy(candidate_prediction, snapshot, history_flags)
        policy_delta, threshold_simulation, divergence_summary = _build_divergence_summary(
            workflow_id=snapshot.workflow_id,
            active_prediction=active_prediction,
            candidate_prediction=candidate_prediction,
            active_policy_path=active_policy_path,
            candidate_policy_path=candidate_policy_path,
        )
        simulation = ReplaySimulationResult(
            workflow_id=snapshot.workflow_id,
            replay_integrity_passed=integrity_status == "passed",
            evaluation_checksum=hash_payload(
                {
                    "workflow_id": snapshot.workflow_id,
                    "feature_snapshot_hash": snapshot.feature_snapshot_hash,
                    "model_input_hash": snapshot.model_input_hash,
                    "active_policy_path": active_policy_path,
                    "candidate_policy_path": candidate_policy_path,
                }
            ),
            feature_snapshot_hash=snapshot.feature_snapshot_hash,
            model_input_hash=snapshot.model_input_hash,
            active_prediction=active_prediction,
            candidate_prediction=candidate_prediction,
            policy_delta=policy_delta,
            threshold_simulation=threshold_simulation,
            divergence_summary_json=divergence_summary,
        )
        persisted_result = persist_model_evaluation_result(
            ModelEvaluationResult(
                evaluation_run_id=int(run.id or 0),
                workflow_id=snapshot.workflow_id,
                feature_snapshot_id=int(snapshot.id or 0),
                replay_integrity_status=integrity_status,
                active_prediction_id=int(persisted_active.id or 0),
                candidate_prediction_id=int(persisted_candidate.id or 0),
                active_policy_path=active_policy_path,
                candidate_policy_path=candidate_policy_path,
                escalation_delta=policy_delta.escalation_delta,
                review_delta=policy_delta.review_delta,
                threshold_delta=policy_delta.threshold_delta,
                severity_delta=policy_delta.severity_delta,
                specialty_delta=policy_delta.specialty_delta,
                calibration_delta=policy_delta.calibration_delta,
                false_negative_risk=policy_delta.false_negative_risk,
                divergence_summary_json={
                    **divergence_summary,
                    "replay_simulation": model_dump(simulation),
                },
            )
        )
        results.append(model_dump(persisted_result))

    workflow_count = len(results)
    divergence_count = sum(1 for item in results if item["escalation_delta"] or item["review_delta"] or item["specialty_delta"])
    escalation_delta = round((sum(1 for item in results if item["escalation_delta"]) / max(workflow_count, 1)) * 100.0, 2)
    false_negative_delta = round((sum(1 for item in results if item["false_negative_risk"]) / max(workflow_count, 1)) * 100.0, 2)
    review_rate_delta = round((sum(1 for item in results if item["review_delta"]) / max(workflow_count, 1)) * 100.0, 2)
    calibration_delta = round(sum(float(item["calibration_delta"]) for item in results) / max(workflow_count, 1) * 100.0, 2)
    evaluation_checksum = _compute_evaluation_checksum(results)
    drift_snapshot = persist_evaluation_drift_snapshot(
        EvaluationDriftSnapshot(
            evaluation_run_id=int(run.id or 0),
            score_distribution_delta=round(sum(abs(float(item["calibration_delta"])) for item in results) / max(workflow_count, 1) * 100.0, 2),
            specialty_distribution_delta=round((sum(1 for item in results if item["specialty_delta"]) / max(workflow_count, 1)) * 100.0, 2),
            review_rate_delta=review_rate_delta,
            escalation_delta=escalation_delta,
            false_negative_delta=false_negative_delta,
            calibration_error_delta=round(abs(calibration_delta), 2),
            created_at=dt.datetime.now().isoformat(timespec="seconds"),
        )
    )
    gate_result = evaluate_promotion_gate(
        profile_key=str(gate_profile["profile_key"]),
        gate_rules=json.loads(str(gate_profile["gate_rules_json"])),
        replay_integrity_passed=integrity_passed,
        drift_snapshot=drift_snapshot,
    )
    summary = EvaluationSummary(
        evaluation_checksum=evaluation_checksum,
        replay_integrity_passed=integrity_passed,
        divergence_count=divergence_count,
        escalation_delta=escalation_delta,
        false_negative_delta=false_negative_delta,
        review_rate_delta=review_rate_delta,
        calibration_delta=calibration_delta,
        promotion_recommendation="promote" if gate_result.passed else "hold",
    )
    final_status = "completed" if integrity_passed else "invalidated"
    update_model_evaluation_run(
        int(run.id or 0),
        status=final_status,
        workflow_count=workflow_count,
        replay_integrity_passed=integrity_passed,
        evaluation_checksum=evaluation_checksum,
        summary_json=summary,
        promotion_recommendation="promote" if gate_result.passed else "hold",
        promotion_gate_result=gate_result,
        completed_at=dt.datetime.now().isoformat(timespec="seconds"),
    )
    record_evaluation_event(
        run.evaluation_run_key,
        action="complete_evaluation",
        decision=final_status,
        payload={
            "evaluation_run_id": run.id,
            "candidate_model_key": str(candidate_model["model_key"]) if candidate_model else str(active_model["model_key"]),
            "threshold_profile_key": candidate_threshold.profile_key if candidate_threshold else active_threshold.profile_key,
            "replay_checksum": evaluation_checksum,
            "integrity_status": final_status,
            "promotion_gate_status": "passed" if gate_result.passed else "blocked",
            "divergence_count": divergence_count,
        },
        confidence=max(0.0, 100.0 - false_negative_delta),
        reasons=gate_result.supporting_evidence,
    )
    return get_model_evaluation_run(int(run.id or 0))


def list_model_evaluations(limit: int = 20) -> list[dict[str, Any]]:
    return [model_dump(build_model_evaluation_run_contract(row)) for row in fetch_model_evaluation_runs(limit=limit)]


def get_model_evaluation_run(run_id: int) -> dict[str, Any]:
    row = fetch_model_evaluation_run(run_id)
    if row is None:
        raise LookupError(f"Evaluation run {run_id} not found.")
    return model_dump(build_model_evaluation_run_contract(row))


def get_model_evaluation_results(run_id: int) -> list[dict[str, Any]]:
    return [model_dump(build_model_evaluation_result_contract(row)) for row in fetch_model_evaluation_results(run_id)]


def get_model_evaluation_drift(run_id: int) -> dict[str, Any] | None:
    row = fetch_evaluation_drift_snapshot(run_id)
    if row is None:
        return None
    return model_dump(build_evaluation_drift_snapshot_contract(row))


def get_model_evaluation_diff(run_id: int) -> dict[str, Any]:
    results = get_model_evaluation_results(run_id)
    return {
        "run_id": run_id,
        "divergence_count": sum(1 for item in results if item["escalation_delta"] or item["review_delta"] or item["specialty_delta"]),
        "results": results,
    }


def get_model_evaluation_promotion_gate(run_id: int) -> dict[str, Any]:
    run = build_model_evaluation_run_contract(fetch_model_evaluation_run(run_id))
    return model_dump(run.promotion_gate_result) if run.promotion_gate_result else {}
