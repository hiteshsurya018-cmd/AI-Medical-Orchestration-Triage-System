from __future__ import annotations

import json

from ..contracts import GovernanceRecommendation, GovernanceTimelineEvent
from ..pydantic_compat import model_dump
from .base import BaseRepository, GovernanceTransactionContext


class GovernanceRepository(BaseRepository):
    transaction_context = GovernanceTransactionContext

    def persist_governance_recommendation(self, recommendation: GovernanceRecommendation) -> GovernanceRecommendation:
        with self.transaction_context() as connection:
            existing = connection.execute(
                "SELECT id FROM governance_recommendations WHERE recommendation_key = :recommendation_key",
                {"recommendation_key": recommendation.recommendation_key},
            ).fetchone()
            if existing is not None:
                payload = model_dump(recommendation)
                payload["id"] = int(existing["id"])
                self.increment_metric("docq_duplicate_governance_recommendation_prevented_total")
                return GovernanceRecommendation(**payload)
            cursor = connection.execute(
                """
                INSERT INTO governance_recommendations (
                    recommendation_key, recommendation_type, source_evaluation_run_id, candidate_model_registry_id,
                    threshold_profile_id, recommendation_status, recommendation_reason, confidence_score,
                    created_at, resolved_at, supporting_evidence_json
                ) VALUES (
                    :recommendation_key, :recommendation_type, :source_evaluation_run_id, :candidate_model_registry_id,
                    :threshold_profile_id, :recommendation_status, :recommendation_reason, :confidence_score,
                    :created_at, :resolved_at, :supporting_evidence_json
                )
                """,
                {
                    "recommendation_key": recommendation.recommendation_key,
                    "recommendation_type": recommendation.recommendation_type,
                    "source_evaluation_run_id": recommendation.source_evaluation_run_id,
                    "candidate_model_registry_id": recommendation.candidate_model_registry_id,
                    "threshold_profile_id": recommendation.threshold_profile_id,
                    "recommendation_status": recommendation.recommendation_status,
                    "recommendation_reason": recommendation.recommendation_reason,
                    "confidence_score": recommendation.confidence_score,
                    "created_at": recommendation.created_at,
                    "resolved_at": recommendation.resolved_at,
                    "supporting_evidence_json": json.dumps(recommendation.supporting_evidence_json, sort_keys=True),
                },
            )
            inserted_id = int(cursor.lastrowid)
        payload = model_dump(recommendation)
        payload["id"] = inserted_id
        return GovernanceRecommendation(**payload)

    def persist_governance_timeline_event(self, event: GovernanceTimelineEvent) -> GovernanceTimelineEvent:
        with self.transaction_context() as connection:
            cursor = connection.execute(
                """
                INSERT INTO governance_timelines (
                    governance_entity_type, governance_entity_id, event_type, event_timestamp, related_model_key,
                    related_threshold_profile_key, incident_correlation_id, payload_json
                ) VALUES (
                    :governance_entity_type, :governance_entity_id, :event_type, :event_timestamp, :related_model_key,
                    :related_threshold_profile_key, :incident_correlation_id, :payload_json
                )
                """,
                {
                    "governance_entity_type": event.governance_entity_type,
                    "governance_entity_id": event.governance_entity_id,
                    "event_type": event.event_type,
                    "event_timestamp": event.event_timestamp,
                    "related_model_key": event.related_model_key,
                    "related_threshold_profile_key": event.related_threshold_profile_key,
                    "incident_correlation_id": event.incident_correlation_id,
                    "payload_json": json.dumps(event.payload_json, sort_keys=True),
                },
            )
            inserted_id = int(cursor.lastrowid)
        payload = model_dump(event)
        payload["id"] = inserted_id
        return GovernanceTimelineEvent(**payload)

    def fetch_governance_recommendations(self, limit: int = 50):
        return self.fetchall(
            """
            SELECT *
            FROM governance_recommendations
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )

    def fetch_governance_recommendation(self, recommendation_id: int):
        return self.fetchone(
            "SELECT * FROM governance_recommendations WHERE id = :recommendation_id",
            {"recommendation_id": recommendation_id},
        )

    def fetch_rollout_profiles(self, limit: int = 20):
        return self.fetchall(
            """
            SELECT *
            FROM governance_rollout_profiles
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )

    def fetch_active_rollout_profile(self):
        return self.fetchone(
            """
            SELECT *
            FROM governance_rollout_profiles
            WHERE status = 'active'
            ORDER BY id DESC
            LIMIT 1
            """
        )

    def fetch_governance_timeline(self, limit: int = 100):
        return self.fetchall(
            """
            SELECT *
            FROM governance_timelines
            ORDER BY event_timestamp DESC, id DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )

    def fetch_drift_trigger_rules(self, limit: int = 50):
        return self.fetchall(
            """
            SELECT *
            FROM drift_trigger_rules
            WHERE status = 'active'
            ORDER BY id ASC
            LIMIT :limit
            """,
            {"limit": limit},
        )
