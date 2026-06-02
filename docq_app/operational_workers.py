from __future__ import annotations

from .advisory_locks import acquire_advisory_lock, release_advisory_lock
from .calendar_integrations import sync_appointment_to_calendar
from .human_coordination import coordination_queue_summary
from .operational_playbooks import handle_notification_failures
from .reminder_runtime import schedule_operational_reminders
from .sla_runtime import scan_sla_violations


def run_reminder_worker(*, worker_id: str) -> dict[str, object]:
    lock = acquire_advisory_lock(lock_key="operational-worker:reminders", owner_id=worker_id, timeout_seconds=120)
    if not lock.acquired:
        return {"executed": False, "reason": lock.detail}
    try:
        return {"executed": True, **schedule_operational_reminders(worker_id=worker_id)}
    finally:
        release_advisory_lock(lock_key="operational-worker:reminders", owner_id=worker_id)


def run_sla_worker(*, worker_id: str) -> dict[str, object]:
    lock = acquire_advisory_lock(lock_key="operational-worker:sla", owner_id=worker_id, timeout_seconds=120)
    if not lock.acquired:
        return {"executed": False, "reason": lock.detail}
    try:
        return {"executed": True, **scan_sla_violations(worker_id=worker_id)}
    finally:
        release_advisory_lock(lock_key="operational-worker:sla", owner_id=worker_id)


def run_playbook_worker(*, worker_id: str) -> dict[str, object]:
    lock = acquire_advisory_lock(lock_key="operational-worker:playbooks", owner_id=worker_id, timeout_seconds=120)
    if not lock.acquired:
        return {"executed": False, "reason": lock.detail}
    try:
        result = handle_notification_failures(worker_id=worker_id)
        result["queue_summary"] = coordination_queue_summary()
        return {"executed": True, **result}
    finally:
        release_advisory_lock(lock_key="operational-worker:playbooks", owner_id=worker_id)


def run_calendar_sync_worker(appointment_id: int, *, provider: str, worker_id: str) -> dict[str, object]:
    lock = acquire_advisory_lock(lock_key=f"operational-worker:calendar:{appointment_id}:{provider}", owner_id=worker_id, timeout_seconds=120)
    if not lock.acquired:
        return {"executed": False, "reason": lock.detail}
    try:
        state = sync_appointment_to_calendar(appointment_id, provider=provider)
        return {"executed": True, "sync_id": state.id, "status": state.sync_status}
    finally:
        release_advisory_lock(lock_key=f"operational-worker:calendar:{appointment_id}:{provider}", owner_id=worker_id)
