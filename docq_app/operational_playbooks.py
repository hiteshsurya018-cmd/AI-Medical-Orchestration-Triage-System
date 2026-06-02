from __future__ import annotations

import datetime as dt

from .appointment_lifecycle import current_lifecycle_state, transition_appointment_lifecycle
from .appointments import fetch_notifications, record_workflow_event
from .human_coordination import enqueue_coordination_item
from .notifications import create_notification


def handle_notification_failures(*, worker_id: str = "playbooks-runtime") -> dict[str, object]:
    processed = 0
    for notification in fetch_notifications(limit=200):
        if notification["status"] != "failed":
            continue
        appointment_id = notification["appointment_id"]
        if appointment_id is None:
            continue
        enqueue_coordination_item(
            queue_type="failed-notification-handling",
            appointment_id=int(appointment_id),
            workflow_id=f"appointment-lifecycle:{appointment_id}",
            priority=90,
            queue_status="pending",
            causation_lineage={"notification_id": int(notification["id"])},
            payload={"channel": notification["channel"], "last_error": notification["last_error"]},
        )
        if current_lifecycle_state(int(appointment_id)) != "incident_recovery_pending":
            transition_appointment_lifecycle(
                int(appointment_id),
                to_state="incident_recovery_pending",
                cause="notification delivery failed",
                actor_name=worker_id,
                actor_role="system",
                escalation_lineage={"notification_id": int(notification["id"]), "channel": str(notification["channel"])},
            )
        processed += 1
    return {"processed": processed}


def handle_no_show_recovery(appointment_id: int, *, worker_id: str = "playbooks-runtime") -> dict[str, object]:
    create_notification(
        appointment_id,
        "patient",
        f"appointment-{appointment_id}",
        "dashboard",
        f"DOCQ no-show recovery initiated for appointment {appointment_id}.",
        "visible",
    )
    if current_lifecycle_state(appointment_id) != "no_show_detected":
        transition_appointment_lifecycle(
            appointment_id,
            to_state="no_show_detected",
            cause="patient did not attend appointment",
            actor_name=worker_id,
            actor_role="system",
        )
    enqueue_coordination_item(
        queue_type="incident_recovery",
        appointment_id=appointment_id,
        workflow_id=f"appointment-lifecycle:{appointment_id}",
        priority=95,
        queue_status="pending",
        causation_lineage={"reason": "no_show_detected"},
        payload={"recovery_started_at": dt.datetime.now().isoformat(timespec="seconds")},
    )
    return {"appointment_id": appointment_id, "recovery": "started"}


def handle_queue_overload(*, backlog_size: int, worker_id: str = "playbooks-runtime") -> dict[str, object]:
    workflow_id = "operations:queue-overload"
    record_workflow_event(
        workflow_id,
        trace_id=workflow_id,
        correlation_id=workflow_id,
        stage="operational-playbooks",
        agent="operational-playbooks",
        action="queue_overload_detected",
        decision="incident_recovery_pending",
        confidence=100.0,
        reasons=[f"backlog size {backlog_size} exceeded playbook threshold"],
        payload={"backlog_size": backlog_size, "worker_id": worker_id},
    )
    return {"workflow_id": workflow_id, "backlog_size": backlog_size}
