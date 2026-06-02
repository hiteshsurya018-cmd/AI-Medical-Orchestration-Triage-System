from __future__ import annotations

import datetime as dt
import json
from typing import Any

from ..ml_governance import hash_payload
from .base import BaseRepository, ReplayTransactionContext


class WorkflowEventRepository(BaseRepository):
    transaction_context = ReplayTransactionContext

    def append_event(
        self,
        *,
        tenant_key: str,
        workflow_id: str,
        trace_id: str,
        correlation_id: str,
        causation_id: int | None,
        parent_event_id: int | None,
        root_event_id: int | None,
        causation_depth: int,
        replay_branch_id: str,
        stage: str,
        agent: str,
        action: str,
        event_type: str,
        decision: str,
        confidence: float | None,
        reasons: list[str],
        payload: dict[str, Any],
    ) -> int:
        created_at = dt.datetime.now().isoformat(timespec="seconds")
        fingerprint = hash_payload(
            {
                "workflow_id": workflow_id,
                "trace_id": trace_id,
                "correlation_id": correlation_id,
                "causation_id": causation_id,
                "parent_event_id": parent_event_id,
                "root_event_id": root_event_id,
                "causation_depth": causation_depth,
                "replay_branch_id": replay_branch_id,
                "stage": stage,
                "agent": agent,
                "action": action,
                "decision": decision,
                "confidence": confidence,
                "reasons": reasons,
                "payload": payload,
            }
        )
        with self.transaction_context() as connection:
            existing = connection.execute(
                "SELECT id FROM workflow_events WHERE event_fingerprint = :event_fingerprint",
                {"event_fingerprint": fingerprint},
            ).fetchone()
            if existing is not None:
                self.increment_metric("docq_duplicate_workflow_event_prevented_total")
                return int(existing["id"])
            cursor = connection.execute(
                """
                INSERT INTO workflow_events (
                    tenant_key, workflow_id, trace_id, correlation_id, causation_id, parent_event_id, root_event_id,
                    causation_depth, replay_branch_id, stage, agent, action, decision, confidence, reasons, payload_json, event_fingerprint, created_at
                ) VALUES (
                    :tenant_key, :workflow_id, :trace_id, :correlation_id, :causation_id, :parent_event_id, :root_event_id,
                    :causation_depth, :replay_branch_id, :stage, :agent, :action, :decision, :confidence, :reasons, :payload_json, :event_fingerprint, :created_at
                )
                """,
                {
                    "tenant_key": tenant_key,
                    "workflow_id": workflow_id,
                    "trace_id": trace_id,
                    "correlation_id": correlation_id,
                    "causation_id": causation_id,
                    "parent_event_id": parent_event_id,
                    "root_event_id": root_event_id,
                    "causation_depth": causation_depth,
                    "replay_branch_id": replay_branch_id,
                    "stage": stage,
                    "agent": agent,
                    "action": action,
                    "decision": decision,
                    "confidence": confidence,
                    "reasons": json.dumps(reasons, sort_keys=True),
                    "payload_json": json.dumps(payload, sort_keys=True),
                    "event_fingerprint": fingerprint,
                    "created_at": created_at,
                },
            )
            event_id = int(cursor.lastrowid)
            resolved_root_event_id = root_event_id or event_id
            if root_event_id is None:
                connection.execute(
                    """
                    UPDATE workflow_events
                    SET root_event_id = :root_event_id
                    WHERE id = :event_id AND root_event_id IS NULL
                    """,
                    {"root_event_id": resolved_root_event_id, "event_id": event_id},
                )
            envelope_payload = {
                "event_id": event_id,
                "workflow_id": workflow_id,
                "trace_id": trace_id,
                "root_event_id": resolved_root_event_id,
                "causation_id": causation_id,
                "replay_branch_id": replay_branch_id,
                "agent": agent,
                "state": stage,
                "action": action,
                "decision": decision,
                "confidence": confidence,
                "reasons": reasons,
                **payload,
            }
            connection.execute(
                """
                INSERT OR IGNORE INTO event_outbox (
                    event_id, schema_version, event_type, aggregate_id, workflow_id, replay_branch_id,
                    trace_id, root_event_id, causation_id, payload_checksum, publish_generation,
                    publish_status, payload_json, created_at
                ) VALUES (
                    :event_id, :schema_version, :event_type, :aggregate_id, :workflow_id, :replay_branch_id,
                    :trace_id, :root_event_id, :causation_id, :payload_checksum, :publish_generation,
                    :publish_status, :payload_json, :created_at
                )
                """,
                {
                    "event_id": event_id,
                    "schema_version": "v1",
                    "event_type": event_type,
                    "aggregate_id": workflow_id,
                    "workflow_id": workflow_id,
                    "replay_branch_id": replay_branch_id,
                    "trace_id": trace_id,
                    "root_event_id": resolved_root_event_id,
                    "causation_id": causation_id,
                    "payload_checksum": hash_payload(envelope_payload),
                    "publish_generation": 0,
                    "publish_status": "pending",
                    "payload_json": json.dumps(envelope_payload, sort_keys=True),
                    "created_at": created_at,
                },
            )
            return event_id

    def fetch_events(self, workflow_id: str, limit: int = 40):
        connection = self.connection()
        try:
            return connection.execute(
                """
                SELECT id, workflow_id, trace_id, correlation_id, causation_id, root_event_id,
                       parent_event_id, causation_depth, replay_branch_id,
                       stage, agent, action, decision, confidence, reasons, payload_json, event_fingerprint, created_at
                FROM workflow_events
                WHERE workflow_id = :workflow_id
                ORDER BY id ASC
                LIMIT :limit
                """,
                {"workflow_id": workflow_id, "limit": limit},
            ).fetchall()
        finally:
            connection.close()

    def fetch_pending_outbox(self, limit: int = 100):
        return self.fetchall(
            """
            SELECT *
            FROM event_outbox
            WHERE publish_status IN ('pending', 'retry')
            ORDER BY id ASC
            LIMIT :limit
            """,
            {"limit": limit},
        )

    def fetch_outbox_after(self, *, last_outbox_id: int, limit: int = 100):
        return self.fetchall(
            """
            SELECT *
            FROM event_outbox
            WHERE id > :last_outbox_id
            ORDER BY id ASC
            LIMIT :limit
            """,
            {"last_outbox_id": last_outbox_id, "limit": limit},
        )

    def mark_outbox_published(self, outbox_id: int, *, generation: int) -> None:
        self.execute_write(
            """
            UPDATE event_outbox
            SET publish_status = :publish_status, published_at = :published_at, publish_generation = :publish_generation
            WHERE id = :outbox_id
            """,
            {
                "publish_status": "published",
                "published_at": dt.datetime.now().isoformat(timespec="seconds"),
                "publish_generation": generation,
                "outbox_id": outbox_id,
            },
        )

    def mark_outbox_retry(self, outbox_id: int, *, generation: int) -> None:
        self.execute_write(
            """
            UPDATE event_outbox
            SET publish_status = :publish_status, publish_generation = :publish_generation
            WHERE id = :outbox_id
            """,
            {"publish_status": "retry", "publish_generation": generation, "outbox_id": outbox_id},
        )
