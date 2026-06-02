from __future__ import annotations

import datetime as dt

from .advisory_locks import acquire_advisory_lock, release_advisory_lock
from .appointment_lifecycle import current_lifecycle_state, transition_appointment_lifecycle
from .appointments import fetch_appointments, get_patient_profile, mark_reminder_delivery, record_workflow_event
from .notifications import RETRY_DELAYS_MINUTES, create_notification
from .observability import metrics_registry
from .worker_runtime import worker_execution_repository


def schedule_operational_reminders(*, worker_id: str = "reminder-runtime") -> dict[str, object]:
    lock = acquire_advisory_lock(lock_key="reminder-runtime:schedule", owner_id=worker_id, timeout_seconds=120)
    if not lock.acquired:
        return {"scheduled": 0, "reason": lock.detail}
    scheduled = 0
    try:
        tomorrow = dt.date.today() + dt.timedelta(days=1)
        for appointment in fetch_appointments(limit=400):
            if appointment["status"] not in {"scheduled", "doctor-acknowledged", "review", "urgent-review"}:
                continue
            appointment_date = dt.date.fromisoformat(str(appointment["appointment_date"]))
            reminder_type = "appointment_reminder" if appointment_date >= tomorrow else "doctor_review_reminder"
            message = _build_reminder_message(appointment, reminder_type)
            profile = get_patient_profile(phone=str(appointment["phone"] or ""), patient_email=str(appointment["patient_email"] or ""))
            preferences = {}
            if profile and profile["communication_preferences_json"]:
                import json

                preferences = json.loads(profile["communication_preferences_json"] or "{}")
            create_notification(
                int(appointment["id"]),
                "patient",
                str(appointment["patient_name"]),
                "sms",
                message,
                "queued",
                correlation_id=f"appointment:{appointment['id']}:sms-{reminder_type}",
                message_category=reminder_type,
            )
            if preferences.get("whatsapp", True):
                create_notification(
                    int(appointment["id"]),
                    "patient",
                    str(appointment["patient_name"]),
                    "whatsapp",
                    message,
                    "queued",
                    correlation_id=f"appointment:{appointment['id']}:whatsapp-{reminder_type}",
                    message_category=reminder_type,
                )
            if appointment["patient_email"]:
                create_notification(
                    int(appointment["id"]),
                    "patient",
                    str(appointment["patient_name"]),
                    "email",
                    message,
                    "queued",
                    correlation_id=f"appointment:{appointment['id']}:email-{reminder_type}",
                    message_category=reminder_type,
                )
            record_workflow_event(
                f"appointment-lifecycle:{appointment['id']}",
                trace_id=f"appointment-lifecycle:{appointment['id']}",
                correlation_id=str(appointment["id"]),
                stage="reminder-runtime",
                agent="reminder-runtime",
                action=f"reminder_scheduled_{reminder_type}",
                decision="reminder_pending",
                confidence=100.0,
                reasons=[f"scheduled by {worker_id}"],
                payload={"appointment_id": int(appointment["id"]), "reminder_type": reminder_type, "lease_token": lock.lock_token},
            )
            current_state = current_lifecycle_state(int(appointment["id"]))
            if current_state in {"appointment_confirmed", "reminder_pending"} and current_state != "reminder_pending":
                transition_appointment_lifecycle(
                    int(appointment["id"]),
                    to_state="reminder_pending",
                    cause=f"{reminder_type} queued",
                    actor_name=worker_id,
                    actor_role="system",
                )
            scheduled += 1
        metrics_registry.increment("docq_operational_reminders_scheduled_total", float(scheduled))
        return {"scheduled": scheduled}
    finally:
        release_advisory_lock(lock_key="reminder-runtime:schedule", owner_id=worker_id)


def record_reminder_outcome(
    appointment_id: int,
    *,
    reminder_type: str,
    delivery_status: str,
    attempts: int,
    worker_id: str = "reminder-runtime",
) -> None:
    mark_reminder_delivery(appointment_id, delivery_status)
    next_attempt_at = None
    if delivery_status == "retry" and attempts <= len(RETRY_DELAYS_MINUTES):
        next_attempt_at = (dt.datetime.now() + dt.timedelta(minutes=RETRY_DELAYS_MINUTES[attempts - 1])).isoformat(timespec="seconds")
    record_workflow_event(
        f"appointment-lifecycle:{appointment_id}",
        trace_id=f"appointment-lifecycle:{appointment_id}",
        correlation_id=str(appointment_id),
        stage="reminder-runtime",
        agent="reminder-runtime",
        action=f"reminder_{delivery_status}",
        decision=delivery_status,
        confidence=100.0,
        reasons=[f"{reminder_type} outcome"],
        payload={
            "appointment_id": appointment_id,
            "reminder_type": reminder_type,
            "delivery_status": delivery_status,
            "attempts": attempts,
            "next_attempt_at": next_attempt_at,
        },
    )


def enqueue_reminder_worker_task(appointment_id: int, reminder_type: str, *, worker_id: str = "reminder-runtime") -> bool:
    task_id = f"reminder:{appointment_id}:{reminder_type}"
    result = worker_execution_repository.record_execution(
        task_id=task_id,
        task_name="reminder_runtime.dispatch",
        workflow_id=f"appointment-lifecycle:{appointment_id}",
        originating_event_id=None,
        idempotency_key=task_id,
        execution_checksum=f"{appointment_id}:{reminder_type}",
        owner_worker_id=worker_id,
        status="queued",
        payload={"appointment_id": appointment_id, "reminder_type": reminder_type},
    )
    return bool(result.created)


def _build_reminder_message(appointment, reminder_type: str) -> str:
    base = f"DOCQ reminder: {appointment['patient_name']} has {appointment['specialty']} with {appointment['doctor_name']} on {appointment['appointment_date']} at {appointment['slot_time']}."
    if reminder_type == "followup_reminder":
        return f"DOCQ follow-up reminder: please continue care for {appointment['patient_name']}."
    if reminder_type == "missed_appointment_reminder":
        return f"DOCQ missed-appointment reminder: {appointment['patient_name']} did not attend the scheduled visit."
    if reminder_type == "governance_review_reminder":
        return f"DOCQ governance review reminder: operational review is pending for appointment {appointment['id']}."
    return base
