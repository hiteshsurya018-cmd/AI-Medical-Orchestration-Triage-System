from __future__ import annotations

import datetime as dt

from .contracts import EventEnvelope
from .db import transaction_scope
from .intelligence_rollups import build_operational_rollup
from .ml_governance import hash_payload
from .projections import apply_event_to_projections


def _upsert_checkpoint(consumer_id: str, *, outbox_id: int, event_id: int, checksum: str) -> None:
    now = dt.datetime.now().isoformat(timespec="seconds")
    with transaction_scope() as connection:
        existing = connection.execute("SELECT id FROM consumer_checkpoints WHERE consumer_id = ?", (consumer_id,)).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO consumer_checkpoints (consumer_id, last_outbox_id, last_event_id, checkpoint_checksum, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (consumer_id, outbox_id, event_id, checksum, now),
            )
        else:
            connection.execute(
                """
                UPDATE consumer_checkpoints
                SET last_outbox_id = ?, last_event_id = ?, checkpoint_checksum = ?, updated_at = ?
                WHERE id = ?
                """,
                (outbox_id, event_id, checksum, now, int(existing["id"])),
            )


def _record_delivery(consumer_id: str, *, outbox_id: int, event_id: int, checksum: str, status: str = "processed") -> None:
    now = dt.datetime.now().isoformat(timespec="seconds")
    with transaction_scope() as connection:
        existing = connection.execute(
            "SELECT id FROM event_delivery_records WHERE outbox_id = ? AND consumer_id = ?",
            (outbox_id, consumer_id),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO event_delivery_records (
                    outbox_id, consumer_id, event_id, processing_checksum, delivery_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (outbox_id, consumer_id, event_id, checksum, status, now, now),
            )
        else:
            connection.execute(
                """
                UPDATE event_delivery_records
                SET processing_checksum = ?, delivery_status = ?, updated_at = ?
                WHERE id = ?
                """,
                (checksum, status, now, int(existing["id"])),
            )


def projection_consumer(envelope: EventEnvelope, *, outbox_id: int) -> None:
    apply_event_to_projections(envelope, outbox_id=outbox_id)
    checksum = hash_payload({"consumer": "projection_consumer", "event_id": envelope.event_id, "outbox_id": outbox_id})
    _record_delivery("projection_consumer", outbox_id=outbox_id, event_id=envelope.event_id, checksum=checksum)
    _upsert_checkpoint("projection_consumer", outbox_id=outbox_id, event_id=envelope.event_id, checksum=checksum)


def intelligence_rollup_consumer(envelope: EventEnvelope, *, outbox_id: int) -> None:
    if envelope.workflow_id.startswith(("ml-governance:", "ml-eval:", "security:")):
        return
    if envelope.event_type not in {"workflow_transition", "policy_decision", "recovery_triggered"}:
        return
    build_operational_rollup()
    checksum = hash_payload({"consumer": "intelligence_rollup_consumer", "event_id": envelope.event_id, "outbox_id": outbox_id})
    _record_delivery("intelligence_rollup_consumer", outbox_id=outbox_id, event_id=envelope.event_id, checksum=checksum)
    _upsert_checkpoint("intelligence_rollup_consumer", outbox_id=outbox_id, event_id=envelope.event_id, checksum=checksum)


def notification_dispatch_consumer(envelope: EventEnvelope, *, outbox_id: int) -> None:
    checksum = hash_payload({"consumer": "notification_dispatch_consumer", "event_id": envelope.event_id, "outbox_id": outbox_id})
    _record_delivery("notification_dispatch_consumer", outbox_id=outbox_id, event_id=envelope.event_id, checksum=checksum)
    _upsert_checkpoint("notification_dispatch_consumer", outbox_id=outbox_id, event_id=envelope.event_id, checksum=checksum)


def governance_trigger_consumer(envelope: EventEnvelope, *, outbox_id: int) -> None:
    if not envelope.workflow_id.startswith("ml-governance:"):
        return
    checksum = hash_payload({"consumer": "governance_trigger_consumer", "event_id": envelope.event_id, "outbox_id": outbox_id})
    _record_delivery("governance_trigger_consumer", outbox_id=outbox_id, event_id=envelope.event_id, checksum=checksum)
    _upsert_checkpoint("governance_trigger_consumer", outbox_id=outbox_id, event_id=envelope.event_id, checksum=checksum)


def telemetry_aggregation_consumer(envelope: EventEnvelope, *, outbox_id: int) -> None:
    checksum = hash_payload({"consumer": "telemetry_aggregation_consumer", "event_id": envelope.event_id, "outbox_id": outbox_id})
    _record_delivery("telemetry_aggregation_consumer", outbox_id=outbox_id, event_id=envelope.event_id, checksum=checksum)
    _upsert_checkpoint("telemetry_aggregation_consumer", outbox_id=outbox_id, event_id=envelope.event_id, checksum=checksum)
