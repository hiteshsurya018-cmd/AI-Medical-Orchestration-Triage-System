from __future__ import annotations

import datetime as dt
import json

from .db import transaction_scope, get_connection
from .observability import metrics_registry


def record_node_heartbeat(
    *,
    node_id: str,
    worker_generation: int = 0,
    stream_generation: int = 0,
    replay_generation: int = 0,
    lease_generation: int = 0,
    status: str = "active",
    metadata: dict[str, object] | None = None,
) -> None:
    now = dt.datetime.now().isoformat(timespec="seconds")
    with transaction_scope() as connection:
        row = connection.execute("SELECT id FROM runtime_nodes WHERE node_id = ?", (node_id,)).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO runtime_nodes (
                    node_id, worker_generation, stream_generation, replay_generation,
                    lease_generation, heartbeat_at, status, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    worker_generation,
                    stream_generation,
                    replay_generation,
                    lease_generation,
                    now,
                    status,
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            )
        else:
            connection.execute(
                """
                UPDATE runtime_nodes
                SET worker_generation = ?, stream_generation = ?, replay_generation = ?,
                    lease_generation = ?, heartbeat_at = ?, status = ?, metadata_json = ?
                WHERE id = ?
                """,
                (
                    worker_generation,
                    stream_generation,
                    replay_generation,
                    lease_generation,
                    now,
                    status,
                    json.dumps(metadata or {}, sort_keys=True),
                    int(row["id"]),
                ),
            )
    metrics_registry.increment("docq_runtime_node_heartbeat_total")


def assign_consumer_ownership(
    *,
    consumer_id: str,
    node_id: str,
    stream_subject: str,
    lease_token: str,
    ownership_generation: int,
    checkpoint_outbox_id: int = 0,
) -> None:
    now = dt.datetime.now().isoformat(timespec="seconds")
    with transaction_scope() as connection:
        row = connection.execute("SELECT id FROM consumer_ownership WHERE consumer_id = ?", (consumer_id,)).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO consumer_ownership (
                    consumer_id, node_id, stream_subject, lease_token,
                    ownership_generation, checkpoint_outbox_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (consumer_id, node_id, stream_subject, lease_token, ownership_generation, checkpoint_outbox_id, now),
            )
        else:
            connection.execute(
                """
                UPDATE consumer_ownership
                SET node_id = ?, stream_subject = ?, lease_token = ?, ownership_generation = ?,
                    checkpoint_outbox_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (node_id, stream_subject, lease_token, ownership_generation, checkpoint_outbox_id, now, int(row["id"])),
            )
    metrics_registry.increment("docq_consumer_ownership_assignments_total")


def list_runtime_nodes() -> list[dict[str, object]]:
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM runtime_nodes ORDER BY heartbeat_at DESC").fetchall()
    return [dict(row) for row in rows]
