from __future__ import annotations

import datetime as dt
import json
from typing import Any

from .contracts import EventEnvelope, ProjectionCheckpoint
from .db import get_connection, transaction_scope
from .ml_governance import hash_payload


def _load_projection(projection_name: str, projection_scope: str = "global") -> dict[str, Any]:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM projection_snapshots WHERE projection_name = ? AND projection_scope = ?",
            (projection_name, projection_scope),
        ).fetchone()
    if row is None:
        return {}
    return json.loads(row["payload_json"] or "{}")


def _save_projection(
    projection_name: str,
    projection_scope: str,
    payload: dict[str, Any],
    *,
    source_outbox_id: int,
    source_event_id: int,
    replay_lineage_metadata: dict[str, Any],
) -> None:
    checksum = hash_payload(payload)
    now = dt.datetime.now().isoformat(timespec="seconds")
    with transaction_scope() as connection:
        existing = connection.execute(
            "SELECT id, projection_generation FROM projection_snapshots WHERE projection_name = ? AND projection_scope = ?",
            (projection_name, projection_scope),
        ).fetchone()
        generation = int(existing["projection_generation"] or 0) + 1 if existing is not None else 1
        if existing is None:
            connection.execute(
                """
                INSERT INTO projection_snapshots (
                    projection_name, projection_scope, payload_json, projection_checksum,
                    source_outbox_id, source_event_id, projection_generation, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    projection_name,
                    projection_scope,
                    json.dumps(payload, sort_keys=True),
                    checksum,
                    source_outbox_id,
                    source_event_id,
                    generation,
                    now,
                    now,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE projection_snapshots
                SET payload_json = ?, projection_checksum = ?, source_outbox_id = ?, source_event_id = ?,
                    projection_generation = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(payload, sort_keys=True),
                    checksum,
                    source_outbox_id,
                    source_event_id,
                    generation,
                    now,
                    int(existing["id"]),
                ),
            )
        checkpoint = connection.execute(
            "SELECT id FROM projection_checkpoints WHERE projection_name = ? AND projection_scope = ?",
            (projection_name, projection_scope),
        ).fetchone()
        lineage_json = json.dumps(replay_lineage_metadata, sort_keys=True)
        if checkpoint is None:
            connection.execute(
                """
                INSERT INTO projection_checkpoints (
                    projection_name, projection_scope, source_outbox_id, source_event_id,
                    projection_generation, projection_checksum, replay_lineage_metadata, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (projection_name, projection_scope, source_outbox_id, source_event_id, generation, checksum, lineage_json, now),
            )
        else:
            connection.execute(
                """
                UPDATE projection_checkpoints
                SET source_outbox_id = ?, source_event_id = ?, projection_generation = ?,
                    projection_checksum = ?, replay_lineage_metadata = ?, updated_at = ?
                WHERE id = ?
                """,
                (source_outbox_id, source_event_id, generation, checksum, lineage_json, now, int(checkpoint["id"])),
            )


def _save_projection_scoped(
    projection_name: str,
    payload: dict[str, Any],
    *,
    source_outbox_id: int,
    source_event_id: int,
    replay_lineage_metadata: dict[str, Any],
    tenant_key: str | None = None,
) -> None:
    _save_projection(
        projection_name,
        "global",
        payload,
        source_outbox_id=source_outbox_id,
        source_event_id=source_event_id,
        replay_lineage_metadata=replay_lineage_metadata,
    )
    if tenant_key:
        _save_projection(
            projection_name,
            f"tenant:{tenant_key}",
            payload,
            source_outbox_id=source_outbox_id,
            source_event_id=source_event_id,
            replay_lineage_metadata={**replay_lineage_metadata, "tenant_key": tenant_key},
        )


def apply_event_to_projections(envelope: EventEnvelope, *, outbox_id: int) -> None:
    tenant_key = str(envelope.payload.get("tenant_key") or "")
    lineage_metadata = {
        "trace_id": envelope.trace_id,
        "root_event_id": envelope.root_event_id,
        "causation_id": envelope.causation_id,
        "replay_branch_id": envelope.replay_branch_id,
        "tenant_key": tenant_key,
    }

    workflow_projection = _load_projection("workflow_projection")
    workflow_stats = workflow_projection.setdefault(
        envelope.workflow_id,
        {"event_count": 0, "latest_state": "", "latest_decision": "", "latest_event_id": 0, "trace_id": envelope.trace_id},
    )
    workflow_stats["event_count"] += 1
    workflow_stats["latest_state"] = str(envelope.payload.get("state", workflow_stats["latest_state"]))
    workflow_stats["latest_decision"] = str(envelope.payload.get("decision", workflow_stats["latest_decision"]))
    workflow_stats["latest_event_id"] = envelope.event_id
    _save_projection_scoped("workflow_projection", workflow_projection, source_outbox_id=outbox_id, source_event_id=envelope.event_id, replay_lineage_metadata=lineage_metadata, tenant_key=tenant_key)

    replay_projection = _load_projection("replay_projection")
    replay_projection[envelope.workflow_id] = {
        "latest_event_id": envelope.event_id,
        "root_event_id": envelope.root_event_id,
        "replay_branch_id": envelope.replay_branch_id,
        "snapshot_reference": envelope.snapshot_reference,
    }
    _save_projection_scoped("replay_projection", replay_projection, source_outbox_id=outbox_id, source_event_id=envelope.event_id, replay_lineage_metadata=lineage_metadata, tenant_key=tenant_key)

    telemetry_projection = _load_projection("telemetry_projection")
    telemetry_projection["event_count"] = int(telemetry_projection.get("event_count", 0)) + 1
    telemetry_projection["latest_trace_id"] = envelope.trace_id
    _save_projection_scoped("telemetry_projection", telemetry_projection, source_outbox_id=outbox_id, source_event_id=envelope.event_id, replay_lineage_metadata=lineage_metadata, tenant_key=tenant_key)

    if envelope.workflow_id.startswith("appointment-lifecycle:"):
        lifecycle_projection = _load_projection("lifecycle_projection")
        lifecycle_projection[str(envelope.payload.get("appointment_id", envelope.workflow_id))] = {
            "workflow_id": envelope.workflow_id,
            "state": str(envelope.payload.get("to_state") or envelope.payload.get("decision") or envelope.payload.get("state") or ""),
            "cause": str(envelope.payload.get("cause") or ""),
            "event_id": envelope.event_id,
            "sla_due_at": envelope.payload.get("sla_due_at"),
        }
        _save_projection_scoped("lifecycle_projection", lifecycle_projection, source_outbox_id=outbox_id, source_event_id=envelope.event_id, replay_lineage_metadata=lineage_metadata, tenant_key=tenant_key)

        reminder_projection = _load_projection("reminder_projection")
        if "reminder_type" in envelope.payload or "delivery_status" in envelope.payload:
            reminder_projection[str(envelope.payload.get("appointment_id", envelope.workflow_id))] = {
                "reminder_type": str(envelope.payload.get("reminder_type") or ""),
                "delivery_status": str(envelope.payload.get("delivery_status") or envelope.payload.get("decision") or ""),
                "attempts": int(envelope.payload.get("attempts", 0) or 0),
                "next_attempt_at": envelope.payload.get("next_attempt_at"),
            }
            _save_projection_scoped("reminder_projection", reminder_projection, source_outbox_id=outbox_id, source_event_id=envelope.event_id, replay_lineage_metadata=lineage_metadata, tenant_key=tenant_key)

        sla_projection = _load_projection("sla_projection")
        if str(envelope.payload.get("agent") or "") == "sla-runtime":
            sla_projection[str(envelope.payload.get("appointment_id", envelope.workflow_id))] = {
                "sla_type": str(envelope.payload.get("sla_type") or ""),
                "observed_minutes": int(envelope.payload.get("observed_minutes", 0) or 0),
                "threshold_minutes": int(envelope.payload.get("threshold_minutes", 0) or 0),
                "action_triggered": str(envelope.payload.get("action_triggered") or ""),
            }
            _save_projection_scoped("sla_projection", sla_projection, source_outbox_id=outbox_id, source_event_id=envelope.event_id, replay_lineage_metadata=lineage_metadata, tenant_key=tenant_key)

        coordination_projection = _load_projection("coordination_projection")
        if str(envelope.payload.get("agent") or "") == "human-coordination" or "queue_type" in envelope.payload:
            coordination_projection[str(envelope.payload.get("appointment_id", envelope.workflow_id))] = {
                "queue_type": str(envelope.payload.get("queue_type") or ""),
                "priority": int(envelope.payload.get("priority", 0) or 0),
                "queue_item_id": envelope.payload.get("queue_item_id"),
            }
            _save_projection_scoped("coordination_projection", coordination_projection, source_outbox_id=outbox_id, source_event_id=envelope.event_id, replay_lineage_metadata=lineage_metadata, tenant_key=tenant_key)

        incident_workflow_projection = _load_projection("incident_workflow_projection")
        if envelope.payload.get("to_state") == "incident_recovery_pending" or "incident" in str(envelope.payload.get("cause", "")).lower():
            incident_workflow_projection[str(envelope.payload.get("appointment_id", envelope.workflow_id))] = {
                "state": str(envelope.payload.get("to_state") or ""),
                "cause": str(envelope.payload.get("cause") or ""),
            }
            _save_projection_scoped("incident_workflow_projection", incident_workflow_projection, source_outbox_id=outbox_id, source_event_id=envelope.event_id, replay_lineage_metadata=lineage_metadata, tenant_key=tenant_key)

        reassignment_projection = _load_projection("reassignment_projection")
        if str(envelope.payload.get("action") or "") == "doctor_reassigned" or envelope.payload.get("new_doctor"):
            reassignment_projection[str(envelope.payload.get("appointment_id", envelope.workflow_id))] = {
                "previous_doctor": str(envelope.payload.get("previous_doctor") or ""),
                "new_doctor": str(envelope.payload.get("new_doctor") or ""),
            }
            _save_projection_scoped("reassignment_projection", reassignment_projection, source_outbox_id=outbox_id, source_event_id=envelope.event_id, replay_lineage_metadata=lineage_metadata, tenant_key=tenant_key)

    if str(envelope.payload.get("agent") or "") == "calendar-integrations" or "calendar_sync_id" in envelope.payload:
        calendar_projection = _load_projection("calendar_sync_projection")
        calendar_projection[str(envelope.payload.get("appointment_id", envelope.workflow_id))] = {
            "provider": str(envelope.payload.get("provider") or ""),
            "sync_direction": str(envelope.payload.get("sync_direction") or ""),
            "calendar_sync_id": envelope.payload.get("calendar_sync_id"),
        }
        _save_projection_scoped("calendar_sync_projection", calendar_projection, source_outbox_id=outbox_id, source_event_id=envelope.event_id, replay_lineage_metadata=lineage_metadata, tenant_key=tenant_key)

    if envelope.workflow_id.startswith("ml-governance:"):
        governance_projection = _load_projection("governance_projection")
        governance_projection["latest_event_id"] = envelope.event_id
        governance_projection["latest_action"] = str(envelope.payload.get("action", ""))
        governance_projection["recommendation_count"] = int(governance_projection.get("recommendation_count", 0)) + 1
        _save_projection_scoped("governance_projection", governance_projection, source_outbox_id=outbox_id, source_event_id=envelope.event_id, replay_lineage_metadata=lineage_metadata, tenant_key=tenant_key)

    if envelope.workflow_id.startswith("ml-eval:"):
        evaluation_projection = _load_projection("evaluation_projection")
        evaluation_projection["latest_event_id"] = envelope.event_id
        evaluation_projection["latest_evaluation_key"] = envelope.workflow_id
        evaluation_projection["event_count"] = int(evaluation_projection.get("event_count", 0)) + 1
        _save_projection_scoped("evaluation_projection", evaluation_projection, source_outbox_id=outbox_id, source_event_id=envelope.event_id, replay_lineage_metadata=lineage_metadata, tenant_key=tenant_key)

    if str(envelope.payload.get("severity", "")).lower() == "critical" or envelope.payload.get("decision") == "emergency_escalation":
        incident_projection = _load_projection("incident_projection")
        incident_projection["critical_count"] = int(incident_projection.get("critical_count", 0)) + 1
        incident_projection["latest_workflow_id"] = envelope.workflow_id
        _save_projection_scoped("incident_projection", incident_projection, source_outbox_id=outbox_id, source_event_id=envelope.event_id, replay_lineage_metadata=lineage_metadata, tenant_key=tenant_key)


def fetch_projection_snapshot(projection_name: str, projection_scope: str = "global") -> dict[str, Any]:
    return _load_projection(projection_name, projection_scope)


def fetch_projection_checkpoint(projection_name: str, projection_scope: str = "global") -> ProjectionCheckpoint | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM projection_checkpoints WHERE projection_name = ? AND projection_scope = ?",
            (projection_name, projection_scope),
        ).fetchone()
    if row is None:
        return None
    return ProjectionCheckpoint(
        id=int(row["id"]),
        projection_name=str(row["projection_name"]),
        projection_scope=str(row["projection_scope"]),
        source_outbox_id=int(row["source_outbox_id"]),
        source_event_id=int(row["source_event_id"]),
        projection_generation=int(row["projection_generation"]),
        projection_checksum=str(row["projection_checksum"]),
        replay_lineage_metadata=json.loads(row["replay_lineage_metadata"] or "{}"),
        updated_at=str(row["updated_at"]),
    )
