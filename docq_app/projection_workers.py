from __future__ import annotations

import datetime as dt
import json

from .advisory_locks import acquire_advisory_lock, release_advisory_lock
from .appointments import record_governance_event
from .contracts import EventEnvelope, EventSchemaVersion
from .event_bus_nats import resolve_event_subject_from_row
from .event_consumers import projection_consumer
from .ml_governance import hash_payload
from .observability import metrics_registry
from .projections import fetch_projection_checkpoint
from .repositories import WorkflowEventRepository


workflow_event_repository = WorkflowEventRepository()


def rebuild_projection(
    projection_name: str,
    *,
    worker_id: str,
    batch_size: int = 200,
) -> dict[str, object]:
    lock = acquire_advisory_lock(lock_key=f"projection-rebuild:{projection_name}", owner_id=worker_id, timeout_seconds=180)
    if not lock.acquired:
        return {"projection_name": projection_name, "rebuilt": False, "reason": lock.detail}
    checkpoint = fetch_projection_checkpoint(projection_name)
    last_outbox_id = int(checkpoint.source_outbox_id) if checkpoint else 0
    rebuilt = 0
    try:
        rows = workflow_event_repository.fetch_outbox_after(last_outbox_id=last_outbox_id, limit=batch_size)
        started_at = dt.datetime.now()
        for row in rows:
            envelope = _build_projection_envelope(row)
            projection_consumer(envelope, outbox_id=int(row["id"]))
            rebuilt += 1
        latency_ms = (dt.datetime.now() - started_at).total_seconds() * 1000.0
        metrics_registry.set_gauge("docq_projection_rebuild_latency_ms", round(latency_ms, 2))
        metrics_registry.increment("docq_projection_rebuild_rows_total", float(rebuilt))
        checksum = hash_payload({"projection_name": projection_name, "rebuilt": rebuilt, "worker_id": worker_id})
        record_governance_event(
            f"projection-rebuild:{projection_name}:{worker_id}",
            action="projection_rebuild_completed",
            decision="accepted",
            payload={
                "projection_name": projection_name,
                "rebuilt_count": rebuilt,
                "lease_token": lock.lock_token,
                "worker_generation": 0,
                "replay_checkpoint_id": checkpoint.id if checkpoint else None,
                "governance_checksum": checksum,
            },
            confidence=100.0,
        )
        return {"projection_name": projection_name, "rebuilt": True, "rebuilt_count": rebuilt, "checksum": checksum}
    finally:
        release_advisory_lock(lock_key=f"projection-rebuild:{projection_name}", owner_id=worker_id)


def _build_projection_envelope(row) -> EventEnvelope:
    payload = json.loads(row["payload_json"] or "{}")
    payload.setdefault("stream_subject", resolve_event_subject_from_row(row))
    return EventEnvelope(
        schema_version=EventSchemaVersion(str(row["schema_version"])),
        event_id=int(row["event_id"]),
        event_type=str(row["event_type"]),
        aggregate_id=str(row["aggregate_id"]),
        workflow_id=str(row["workflow_id"]),
        trace_id=str(row["trace_id"]),
        root_event_id=payload.get("root_event_id"),
        causation_id=row["causation_id"],
        replay_branch_id=str(row["replay_branch_id"]),
        replay_checksum=hash_payload(
            {
                "workflow_id": row["workflow_id"],
                "root_event_id": payload.get("root_event_id"),
                "replay_branch_id": row["replay_branch_id"],
                "payload_checksum": row["payload_checksum"],
            }
        ),
        payload_checksum=str(row["payload_checksum"]),
        governance_context=payload.get("governance_context", {}),
        evaluation_context=payload.get("evaluation_context", {}),
        worker_generation=int(payload.get("worker_generation", 0) or 0),
        snapshot_reference=payload.get("snapshot_reference"),
        payload=payload,
        created_at=str(row["created_at"]),
    )
