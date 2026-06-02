from __future__ import annotations

import datetime as dt
import json
import sqlite3

from .db import get_connection
from .ml_governance import hash_payload
from .observability import metrics_registry

EVALUATION_PREFIX = "ml-eval:%"
GOVERNANCE_PREFIX = "ml-governance:%"
SECURITY_PREFIX = "security:%"


def _serialize_rollup_row(row) -> dict[str, object]:
    return {
        "rollup_id": int(row["id"]),
        "rollup_key": str(row["rollup_key"]),
        "rollup_checksum": str(row["rollup_checksum"]),
        "payload": json.loads(row["payload_json"] or "{}"),
        "created_at": str(row["created_at"]),
    }


def build_operational_rollup() -> dict[str, object]:
    started = dt.datetime.now()
    with get_connection() as connection:
        workflow_count = int(
            connection.execute(
                """
                SELECT COUNT(DISTINCT workflow_id)
                FROM workflow_events
                WHERE workflow_id NOT LIKE ?
                  AND workflow_id NOT LIKE ?
                  AND workflow_id NOT LIKE ?
                """,
                (EVALUATION_PREFIX, GOVERNANCE_PREFIX, SECURITY_PREFIX),
            ).fetchone()[0]
            or 0
        )
        incident_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM governance_recommendations WHERE recommendation_type IN ('rollback', 'drift_alert')"
            ).fetchone()[0]
            or 0
        )
        review_queue = int(
            connection.execute("SELECT COUNT(*) FROM appointments WHERE queue_state IN ('manual-review', 'assistant-review', 'priority-review')").fetchone()[0]
            or 0
        )
        worker_backlog = int(
            connection.execute("SELECT COUNT(*) FROM worker_execution_ledger WHERE execution_state IN ('started', 'queued')").fetchone()[0]
            or 0
        )
        replay_latency_depth = int(
            connection.execute(
                """
                SELECT AVG(event_count)
                FROM (
                    SELECT COUNT(*) AS event_count
                    FROM workflow_events
                    WHERE workflow_id NOT LIKE ?
                      AND workflow_id NOT LIKE ?
                      AND workflow_id NOT LIKE ?
                    GROUP BY workflow_id
                )
                """,
                (EVALUATION_PREFIX, GOVERNANCE_PREFIX, SECURITY_PREFIX),
            ).fetchone()[0]
            or 0
        )
        payload = {
            "workflow_health": {"workflow_count": workflow_count, "review_queue": review_queue},
            "governance_readiness": {
                "incident_count": incident_count,
                "active_recommendations": int(connection.execute("SELECT COUNT(*) FROM governance_recommendations WHERE recommendation_status = 'pending'").fetchone()[0] or 0),
            },
            "replay_latency": {"average_depth": replay_latency_depth},
            "worker_backlog": worker_backlog,
        }
        source_checksum = hash_payload(payload)
        rollup_checksum = hash_payload({"source_checksum": source_checksum, "payload": payload})
        created_at = dt.datetime.now().isoformat(timespec="microseconds")
        rollup_key = f"ops-global-{source_checksum[:16]}"
        payload_json = json.dumps(payload, sort_keys=True)
        existing = connection.execute(
            """
            SELECT *
            FROM intelligence_rollups
            WHERE rollup_key = ? OR (rollup_type = ? AND rollup_scope = ? AND source_checksum = ?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (rollup_key, "operational", "global", source_checksum),
        ).fetchone()
        if existing is None:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO intelligence_rollups (
                        rollup_key, rollup_type, rollup_scope, source_checksum, rollup_checksum, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (rollup_key, "operational", "global", source_checksum, rollup_checksum, payload_json, created_at),
                )
                rollup_id = int(cursor.lastrowid)
                persisted = {
                    "rollup_id": rollup_id,
                    "rollup_key": rollup_key,
                    "rollup_checksum": rollup_checksum,
                    "payload": payload,
                    "created_at": created_at,
                }
            except sqlite3.IntegrityError:
                existing = connection.execute(
                    """
                    SELECT *
                    FROM intelligence_rollups
                    WHERE rollup_key = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (rollup_key,),
                ).fetchone()
                if existing is None:
                    raise
                persisted = _serialize_rollup_row(existing)
                rollup_id = int(persisted["rollup_id"])
        else:
            persisted = _serialize_rollup_row(existing)
            rollup_id = int(persisted["rollup_id"])
        connection.execute(
            """
            INSERT INTO rollup_generation_metadata (
                rollup_key, generation_status, workflow_count, rollup_checksum, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (rollup_key, "completed", workflow_count, rollup_checksum, created_at, created_at),
        )
    elapsed_ms = (dt.datetime.now() - started).total_seconds() * 1000.0
    metrics_registry.set_gauge("docq_rollup_generation_latency_ms", round(elapsed_ms, 2))
    from .appointments import record_governance_event

    record_governance_event(
        f"rollup-{rollup_key}",
        action="intelligence_rollup_generated",
        decision="accepted",
        payload={
            "rollup_generation_id": rollup_id,
            "rollup_key": rollup_key,
            "rollup_checksum": rollup_checksum,
            "workflow_count": workflow_count,
        },
        confidence=100.0,
    )
    return {
        "rollup_id": rollup_id,
        "rollup_key": str(persisted["rollup_key"]),
        "rollup_checksum": str(persisted["rollup_checksum"]),
        "payload": persisted["payload"],
        "created_at": str(persisted["created_at"]),
    }


def fetch_latest_rollup(rollup_type: str = "operational") -> dict[str, object] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM intelligence_rollups
            WHERE rollup_type = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (rollup_type,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "rollup_key": str(row["rollup_key"]),
        "rollup_type": str(row["rollup_type"]),
        "rollup_scope": str(row["rollup_scope"]),
        "source_checksum": str(row["source_checksum"]),
        "rollup_checksum": str(row["rollup_checksum"]),
        "payload": json.loads(row["payload_json"] or "{}"),
        "created_at": str(row["created_at"]),
    }
