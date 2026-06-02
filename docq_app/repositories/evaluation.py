from __future__ import annotations

import json

from ..contracts import EvaluationDriftSnapshot, ModelEvaluationResult, ModelEvaluationRun, PromotionGateResult
from ..pydantic_compat import model_dump
from .base import BaseRepository, EvaluationTransactionContext


class EvaluationRepository(BaseRepository):
    transaction_context = EvaluationTransactionContext

    def persist_model_evaluation_run(self, run: ModelEvaluationRun) -> ModelEvaluationRun:
        with self.transaction_context() as connection:
            existing = connection.execute(
                "SELECT id FROM model_evaluation_runs WHERE evaluation_run_key = :evaluation_run_key",
                {"evaluation_run_key": run.evaluation_run_key},
            ).fetchone()
            if existing is not None:
                payload = model_dump(run)
                payload["id"] = int(existing["id"])
                self.increment_metric("docq_duplicate_model_evaluation_prevented_total")
                return ModelEvaluationRun(**payload)
            cursor = connection.execute(
                """
                INSERT INTO model_evaluation_runs (
                    evaluation_run_key, candidate_model_registry_id, active_model_registry_id,
                    candidate_threshold_profile_id, active_threshold_profile_id, evaluation_scope, workflow_count,
                    started_at, completed_at, status, replay_integrity_passed, evaluation_checksum,
                    summary_json, promotion_recommendation, promotion_gate_result
                ) VALUES (
                    :evaluation_run_key, :candidate_model_registry_id, :active_model_registry_id,
                    :candidate_threshold_profile_id, :active_threshold_profile_id, :evaluation_scope, :workflow_count,
                    :started_at, :completed_at, :status, :replay_integrity_passed, :evaluation_checksum,
                    :summary_json, :promotion_recommendation, :promotion_gate_result
                )
                """,
                {
                    "evaluation_run_key": run.evaluation_run_key,
                    "candidate_model_registry_id": run.candidate_model_registry_id,
                    "active_model_registry_id": run.active_model_registry_id,
                    "candidate_threshold_profile_id": run.candidate_threshold_profile_id,
                    "active_threshold_profile_id": run.active_threshold_profile_id,
                    "evaluation_scope": run.evaluation_scope,
                    "workflow_count": run.workflow_count,
                    "started_at": run.started_at,
                    "completed_at": run.completed_at,
                    "status": run.status,
                    "replay_integrity_passed": 1 if run.replay_integrity_passed else 0,
                    "evaluation_checksum": run.evaluation_checksum,
                    "summary_json": json.dumps(model_dump(run.summary_json), sort_keys=True),
                    "promotion_recommendation": run.promotion_recommendation,
                    "promotion_gate_result": json.dumps(model_dump(run.promotion_gate_result), sort_keys=True) if run.promotion_gate_result else None,
                },
            )
            run_id = int(cursor.lastrowid)
        payload = model_dump(run)
        payload["id"] = run_id
        return ModelEvaluationRun(**payload)

    def update_model_evaluation_run(
        self,
        run_id: int,
        *,
        status: str,
        workflow_count: int,
        replay_integrity_passed: bool,
        evaluation_checksum: str,
        summary_json,
        promotion_recommendation: str,
        promotion_gate_result: PromotionGateResult | None,
        completed_at: str | None,
    ) -> None:
        with self.transaction_context() as connection:
            connection.execute(
                """
                UPDATE model_evaluation_runs
                SET status = :status, workflow_count = :workflow_count,
                    replay_integrity_passed = :replay_integrity_passed, evaluation_checksum = :evaluation_checksum,
                    summary_json = :summary_json, promotion_recommendation = :promotion_recommendation,
                    promotion_gate_result = :promotion_gate_result, completed_at = :completed_at
                WHERE id = :run_id
                """,
                {
                    "status": status,
                    "workflow_count": workflow_count,
                    "replay_integrity_passed": 1 if replay_integrity_passed else 0,
                    "evaluation_checksum": evaluation_checksum,
                    "summary_json": json.dumps(model_dump(summary_json), sort_keys=True),
                    "promotion_recommendation": promotion_recommendation,
                    "promotion_gate_result": json.dumps(model_dump(promotion_gate_result), sort_keys=True) if promotion_gate_result else None,
                    "completed_at": completed_at,
                    "run_id": run_id,
                },
            )

    def persist_model_evaluation_result(self, result: ModelEvaluationResult) -> ModelEvaluationResult:
        with self.transaction_context() as connection:
            cursor = connection.execute(
                """
                INSERT INTO model_evaluation_results (
                    evaluation_run_id, workflow_id, feature_snapshot_id, replay_integrity_status, active_prediction_id,
                    candidate_prediction_id, active_policy_path, candidate_policy_path, escalation_delta, review_delta,
                    threshold_delta, severity_delta, specialty_delta, calibration_delta, false_negative_risk,
                    divergence_summary_json
                ) VALUES (
                    :evaluation_run_id, :workflow_id, :feature_snapshot_id, :replay_integrity_status, :active_prediction_id,
                    :candidate_prediction_id, :active_policy_path, :candidate_policy_path, :escalation_delta, :review_delta,
                    :threshold_delta, :severity_delta, :specialty_delta, :calibration_delta, :false_negative_risk,
                    :divergence_summary_json
                )
                """,
                {
                    "evaluation_run_id": result.evaluation_run_id,
                    "workflow_id": result.workflow_id,
                    "feature_snapshot_id": result.feature_snapshot_id,
                    "replay_integrity_status": result.replay_integrity_status,
                    "active_prediction_id": result.active_prediction_id,
                    "candidate_prediction_id": result.candidate_prediction_id,
                    "active_policy_path": result.active_policy_path,
                    "candidate_policy_path": result.candidate_policy_path,
                    "escalation_delta": 1 if result.escalation_delta else 0,
                    "review_delta": 1 if result.review_delta else 0,
                    "threshold_delta": result.threshold_delta,
                    "severity_delta": result.severity_delta,
                    "specialty_delta": result.specialty_delta,
                    "calibration_delta": result.calibration_delta,
                    "false_negative_risk": 1 if result.false_negative_risk else 0,
                    "divergence_summary_json": json.dumps(result.divergence_summary_json, sort_keys=True),
                },
            )
            result_id = int(cursor.lastrowid)
        payload = model_dump(result)
        payload["id"] = result_id
        return ModelEvaluationResult(**payload)

    def persist_evaluation_drift_snapshot(self, snapshot: EvaluationDriftSnapshot) -> EvaluationDriftSnapshot:
        with self.transaction_context() as connection:
            cursor = connection.execute(
                """
                INSERT INTO evaluation_drift_snapshots (
                    evaluation_run_id, score_distribution_delta, specialty_distribution_delta, review_rate_delta,
                    escalation_delta, false_negative_delta, calibration_error_delta, created_at
                ) VALUES (
                    :evaluation_run_id, :score_distribution_delta, :specialty_distribution_delta, :review_rate_delta,
                    :escalation_delta, :false_negative_delta, :calibration_error_delta, :created_at
                )
                """,
                {
                    "evaluation_run_id": snapshot.evaluation_run_id,
                    "score_distribution_delta": snapshot.score_distribution_delta,
                    "specialty_distribution_delta": snapshot.specialty_distribution_delta,
                    "review_rate_delta": snapshot.review_rate_delta,
                    "escalation_delta": snapshot.escalation_delta,
                    "false_negative_delta": snapshot.false_negative_delta,
                    "calibration_error_delta": snapshot.calibration_error_delta,
                    "created_at": snapshot.created_at,
                },
            )
            snapshot_id = int(cursor.lastrowid)
        payload = model_dump(snapshot)
        payload["id"] = snapshot_id
        return EvaluationDriftSnapshot(**payload)

    def fetch_model_evaluation_runs(self, limit: int = 20):
        return self.fetchall(
            """
            SELECT *
            FROM model_evaluation_runs
            ORDER BY started_at DESC, id DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )

    def fetch_model_evaluation_run(self, run_id: int):
        return self.fetchone("SELECT * FROM model_evaluation_runs WHERE id = :run_id", {"run_id": run_id})

    def fetch_model_evaluation_results(self, run_id: int):
        return self.fetchall(
            """
            SELECT *
            FROM model_evaluation_results
            WHERE evaluation_run_id = :run_id
            ORDER BY id ASC
            """,
            {"run_id": run_id},
        )

    def fetch_evaluation_drift_snapshot(self, run_id: int):
        return self.fetchone(
            """
            SELECT *
            FROM evaluation_drift_snapshots
            WHERE evaluation_run_id = :run_id
            ORDER BY id DESC
            LIMIT 1
            """,
            {"run_id": run_id},
        )
