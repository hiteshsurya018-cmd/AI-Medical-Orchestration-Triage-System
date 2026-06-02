from __future__ import annotations

import datetime as dt
import json

from .appointments import get_appointment, record_workflow_event
from .contracts import AppointmentLifecycleTransition
from .db import get_connection
from .pydantic_compat import model_dump

LIFECYCLE_PREFIX = "appointment-lifecycle:"

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "intake_created": {"intake_review_pending", "triage_completed", "workflow_closed"},
    "intake_review_pending": {"triage_completed", "escalation_required", "workflow_closed"},
    "triage_completed": {"scheduling_pending", "appointment_confirmed", "escalation_required"},
    "scheduling_pending": {"appointment_confirmed", "reassignment_pending", "escalation_required"},
    "appointment_confirmed": {"reminder_pending", "patient_checked_in", "no_show_detected", "reassignment_pending", "incident_recovery_pending", "escalation_required"},
    "reminder_pending": {"patient_checked_in", "no_show_detected", "followup_required", "reassignment_pending", "incident_recovery_pending", "escalation_required"},
    "patient_checked_in": {"consultation_in_progress", "appointment_completed"},
    "consultation_in_progress": {"followup_required", "appointment_completed", "escalation_required"},
    "followup_required": {"workflow_closed", "appointment_confirmed"},
    "escalation_required": {"incident_recovery_pending", "workflow_closed", "reassignment_pending"},
    "no_show_detected": {"followup_required", "workflow_closed", "incident_recovery_pending"},
    "reassignment_pending": {"appointment_confirmed", "workflow_closed"},
    "incident_recovery_pending": {"workflow_closed", "appointment_confirmed", "no_show_detected"},
    "appointment_completed": {"workflow_closed", "followup_required"},
    "workflow_closed": set(),
}

DEFAULT_SLA_MINUTES = {
    "intake_review_pending": 15,
    "scheduling_pending": 20,
    "appointment_confirmed": 60,
    "reminder_pending": 120,
    "escalation_required": 10,
    "reassignment_pending": 15,
    "incident_recovery_pending": 15,
}


def initialize_appointment_lifecycle(appointment: dict[str, object], *, actor_name: str, actor_role: str) -> AppointmentLifecycleTransition:
    queue_state = str(appointment.get("queue_state") or "")
    status = str(appointment.get("status") or "")
    if queue_state in {"manual-review", "assistant-review", "priority-review"} or status == "review":
        target_state = "intake_review_pending"
        cause = "initial review required"
    else:
        target_state = "appointment_confirmed"
        cause = "appointment created"
    return transition_appointment_lifecycle(
        int(appointment["id"]),
        to_state=target_state,
        cause=cause,
        actor_name=actor_name,
        actor_role=actor_role,
    )


def current_lifecycle_state(appointment_id: int) -> str | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT to_state FROM appointment_lifecycle_transitions WHERE appointment_id = ? ORDER BY id DESC LIMIT 1",
            (appointment_id,),
        ).fetchone()
    return str(row["to_state"]) if row is not None else None


def transition_appointment_lifecycle(
    appointment_id: int,
    *,
    to_state: str,
    cause: str,
    actor_name: str,
    actor_role: str,
    escalation_lineage: dict[str, object] | None = None,
) -> AppointmentLifecycleTransition:
    now = dt.datetime.now().isoformat(timespec="seconds")
    appointment = get_appointment(appointment_id)
    if appointment is None:
        raise ValueError(f"appointment {appointment_id} not found")
    tenant_key = str(appointment["tenant_key"] or "default-clinic")
    workflow_id = f"{LIFECYCLE_PREFIX}{appointment_id}"
    from_state = current_lifecycle_state(appointment_id)
    if from_state is not None and to_state not in ALLOWED_TRANSITIONS.get(from_state, set()):
        raise ValueError(f"invalid lifecycle transition {from_state} -> {to_state}")
    sla_due_at = None
    if to_state in DEFAULT_SLA_MINUTES:
        sla_due_at = (dt.datetime.now() + dt.timedelta(minutes=DEFAULT_SLA_MINUTES[to_state])).isoformat(timespec="seconds")
    payload = {
        "appointment_id": appointment_id,
        "from_state": from_state,
        "to_state": to_state,
        "cause": cause,
        "responsible_actor": actor_name,
        "responsible_role": actor_role,
        "sla_due_at": sla_due_at,
        "escalation_lineage": escalation_lineage or {},
    }
    event_id = record_workflow_event(
        workflow_id,
        trace_id=workflow_id,
        correlation_id=str(appointment_id),
        stage="appointment-lifecycle",
        agent="appointment-lifecycle",
        action=f"transition_{to_state}",
        decision=to_state,
        confidence=100.0,
        reasons=[cause],
        payload=payload,
    )
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO appointment_lifecycle_transitions (
                appointment_id, tenant_key, workflow_id, from_state, to_state, cause, responsible_actor,
                responsible_role, event_id, sla_due_at, escalation_lineage, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                appointment_id,
                tenant_key,
                workflow_id,
                from_state,
                to_state,
                cause,
                actor_name,
                actor_role,
                event_id,
                sla_due_at,
                json.dumps(escalation_lineage or {}, sort_keys=True),
                now,
            ),
        )
        transition_id = int(cursor.lastrowid)
    return AppointmentLifecycleTransition(
        id=transition_id,
        appointment_id=appointment_id,
        workflow_id=workflow_id,
        from_state=from_state or "",
        to_state=to_state,
        cause=cause,
        responsible_actor=actor_name,
        responsible_role=actor_role,
        event_id=event_id,
        sla_due_at=sla_due_at,
        escalation_lineage=escalation_lineage or {},
        created_at=now,
    )


def reconcile_lifecycle_from_status(
    appointment_id: int,
    *,
    queue_state: str | None,
    status: str | None,
    follow_up_status: str | None,
    actor_name: str,
    actor_role: str,
) -> AppointmentLifecycleTransition | None:
    normalized_status = str(status or "").lower()
    normalized_queue = str(queue_state or "").lower()
    normalized_followup = str(follow_up_status or "").lower()
    target_state = None
    cause = None
    if normalized_status == "cancelled":
        target_state, cause = "workflow_closed", "appointment cancelled"
    elif normalized_status in {"doctor-acknowledged", "scheduled"}:
        target_state, cause = "appointment_confirmed", "appointment confirmed"
    elif normalized_status == "checked-in":
        target_state, cause = "patient_checked_in", "patient checked in"
    elif normalized_status in {"urgent-review", "follow-up"} or normalized_queue == "priority-review":
        target_state, cause = "escalation_required", "operational escalation required"
    elif normalized_followup == "requested":
        target_state, cause = "followup_required", "doctor follow-up requested"
    elif normalized_queue in {"manual-review", "assistant-review"}:
        target_state, cause = "intake_review_pending", "manual review queue assignment"
    elif normalized_status == "rescheduled":
        target_state, cause = "scheduling_pending", "appointment rescheduled"
    if target_state is None:
        return None
    current = current_lifecycle_state(appointment_id)
    if current == target_state:
        return None
    return transition_appointment_lifecycle(
        appointment_id,
        to_state=target_state,
        cause=cause or "status synchronization",
        actor_name=actor_name,
        actor_role=actor_role,
    )


def reconstruct_operational_state(appointment_id: int) -> dict[str, object]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM appointment_lifecycle_transitions WHERE appointment_id = ? ORDER BY id ASC",
            (appointment_id,),
        ).fetchall()
    transitions = [
        AppointmentLifecycleTransition(
            id=int(row["id"]),
            appointment_id=int(row["appointment_id"]),
            workflow_id=str(row["workflow_id"]),
            from_state=str(row["from_state"] or ""),
            to_state=str(row["to_state"]),
            cause=str(row["cause"]),
            responsible_actor=str(row["responsible_actor"]),
            responsible_role=str(row["responsible_role"]),
            event_id=int(row["event_id"]) if row["event_id"] is not None else None,
            sla_due_at=str(row["sla_due_at"]) if row["sla_due_at"] else None,
            escalation_lineage=json.loads(row["escalation_lineage"] or "{}"),
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]
    current = transitions[-1].to_state if transitions else ""
    return {
        "appointment_id": appointment_id,
        "current_state": current,
        "transition_count": len(transitions),
        "transitions": [model_dump(item) for item in transitions],
    }
