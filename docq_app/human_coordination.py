from __future__ import annotations

import datetime as dt
import json

from .advisory_locks import acquire_advisory_lock, release_advisory_lock
from .appointment_lifecycle import transition_appointment_lifecycle
from .appointments import fetch_coordination_queue_items, get_appointment, record_workflow_event
from .contracts import CoordinationQueueItem
from .db import get_connection
from .tenancy import get_current_tenant_key


def enqueue_coordination_item(
    *,
    queue_type: str,
    appointment_id: int | None,
    workflow_id: str,
    priority: int,
    queue_status: str,
    causation_lineage: dict[str, object] | None = None,
    payload: dict[str, object] | None = None,
) -> CoordinationQueueItem:
    now = dt.datetime.now().isoformat(timespec="seconds")
    tenant_key = get_current_tenant_key()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO coordination_queue_items (
                queue_type, appointment_id, tenant_key, workflow_id, priority, queue_status, assigned_owner,
                causation_lineage, payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                queue_type,
                appointment_id,
                tenant_key,
                workflow_id,
                priority,
                queue_status,
                None,
                json.dumps(causation_lineage or {}, sort_keys=True),
                json.dumps(payload or {}, sort_keys=True),
                now,
                now,
            ),
        )
        item_id = int(cursor.lastrowid)
    record_workflow_event(
        workflow_id,
        trace_id=workflow_id,
        correlation_id=str(appointment_id or workflow_id),
        stage="human-coordination",
        agent="human-coordination",
        action=f"{queue_type}_queued",
        decision=queue_status,
        confidence=100.0,
        reasons=[f"{queue_type} queue item created"],
        payload={"queue_type": queue_type, "priority": priority, "queue_item_id": item_id, **(payload or {})},
    )
    return CoordinationQueueItem(
        id=item_id,
        queue_type=queue_type,
        appointment_id=appointment_id,
        workflow_id=workflow_id,
        priority=priority,
        queue_status=queue_status,
        assigned_owner=None,
        causation_lineage=causation_lineage or {},
        payload_json=payload or {},
        created_at=now,
        updated_at=now,
    )


def assign_queue_item(queue_item_id: int, *, owner: str, worker_id: str = "coordination-runtime") -> CoordinationQueueItem:
    lock = acquire_advisory_lock(lock_key=f"coordination-item:{queue_item_id}", owner_id=worker_id, timeout_seconds=120)
    if not lock.acquired:
        raise ValueError(lock.detail)
    try:
        now = dt.datetime.now().isoformat(timespec="seconds")
        with get_connection() as connection:
            row = connection.execute("SELECT * FROM coordination_queue_items WHERE id = ?", (queue_item_id,)).fetchone()
            if row is None:
                raise ValueError(f"coordination queue item {queue_item_id} not found")
            connection.execute(
                "UPDATE coordination_queue_items SET assigned_owner = ?, updated_at = ? WHERE id = ?",
                (owner, now, queue_item_id),
            )
            updated = connection.execute("SELECT * FROM coordination_queue_items WHERE id = ?", (queue_item_id,)).fetchone()
        return _build_queue_contract(updated)
    finally:
        release_advisory_lock(lock_key=f"coordination-item:{queue_item_id}", owner_id=worker_id)


def deterministic_reassign_appointment(
    appointment_id: int,
    *,
    candidate_doctors: list[str],
    actor_name: str,
    actor_role: str,
) -> str:
    appointment = get_appointment(appointment_id)
    if appointment is None:
        raise ValueError(f"appointment {appointment_id} not found")
    current_doctor = str(appointment["doctor_name"])
    ordered = sorted(dict.fromkeys([name for name in candidate_doctors if name and name != current_doctor]))
    if not ordered:
        ordered = [current_doctor]
    selected = ordered[0]
    with get_connection() as connection:
        connection.execute("UPDATE appointments SET doctor_name = ?, queue_state = ? WHERE id = ?", (selected, "awaiting-doctor", appointment_id))
    transition_appointment_lifecycle(
        appointment_id,
        to_state="reassignment_pending",
        cause=f"deterministic reassignment to {selected}",
        actor_name=actor_name,
        actor_role=actor_role,
        escalation_lineage={"previous_doctor": current_doctor, "new_doctor": selected},
    )
    record_workflow_event(
        f"appointment-lifecycle:{appointment_id}",
        trace_id=f"appointment-lifecycle:{appointment_id}",
        correlation_id=str(appointment_id),
        stage="human-coordination",
        agent="human-coordination",
        action="doctor_reassigned",
        decision="reassignment_pending",
        confidence=100.0,
        reasons=[f"reassigned from {current_doctor} to {selected}"],
        payload={"appointment_id": appointment_id, "previous_doctor": current_doctor, "new_doctor": selected},
    )
    return selected


def coordination_queue_summary() -> dict[str, object]:
    items = fetch_coordination_queue_items(limit=200)
    summary: dict[str, int] = {}
    for row in items:
        key = str(row["queue_type"])
        summary[key] = summary.get(key, 0) + 1
    return {"total": len(items), "by_queue": summary}


def _build_queue_contract(row) -> CoordinationQueueItem:
    return CoordinationQueueItem(
        id=int(row["id"]),
        queue_type=str(row["queue_type"]),
        appointment_id=int(row["appointment_id"]) if row["appointment_id"] is not None else None,
        workflow_id=str(row["workflow_id"]),
        priority=int(row["priority"]),
        queue_status=str(row["queue_status"]),
        assigned_owner=str(row["assigned_owner"]) if row["assigned_owner"] else None,
        causation_lineage=json.loads(row["causation_lineage"] or "{}"),
        payload_json=json.loads(row["payload_json"] or "{}"),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
