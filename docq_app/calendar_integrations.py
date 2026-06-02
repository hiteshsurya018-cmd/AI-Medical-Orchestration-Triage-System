from __future__ import annotations

import datetime as dt
import json

from .appointments import get_appointment, record_workflow_event
from .contracts import CalendarSyncState
from .db import get_connection
from .tenancy import get_current_tenant_key


def sync_appointment_to_calendar(
    appointment_id: int,
    *,
    provider: str,
    sync_direction: str = "push",
    external_ref: str | None = None,
    payload: dict[str, object] | None = None,
) -> CalendarSyncState:
    appointment = get_appointment(appointment_id)
    if appointment is None:
        raise ValueError(f"appointment {appointment_id} not found")
    created_at = dt.datetime.now().isoformat(timespec="seconds")
    provider_normalized = provider.lower()
    tenant_key = get_current_tenant_key()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO calendar_sync_runs (
                appointment_id, tenant_key, provider, sync_direction, sync_status, external_ref,
                conflict_detected, retry_count, payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                appointment_id,
                tenant_key,
                provider_normalized,
                sync_direction,
                "synced",
                external_ref,
                0,
                0,
                json.dumps(payload or {}, sort_keys=True),
                created_at,
                created_at,
            ),
        )
        sync_id = int(cursor.lastrowid)
    record_workflow_event(
        f"appointment-lifecycle:{appointment_id}",
        trace_id=f"appointment-lifecycle:{appointment_id}",
        correlation_id=str(appointment_id),
        stage="calendar-sync",
        agent="calendar-integrations",
        action=f"{provider_normalized}_calendar_sync",
        decision="appointment_confirmed",
        confidence=100.0,
        reasons=[f"{provider_normalized} calendar synchronized"],
        payload={
            "appointment_id": appointment_id,
            "tenant_key": tenant_key,
            "provider": provider_normalized,
            "sync_direction": sync_direction,
            "external_ref": external_ref,
            "calendar_sync_id": sync_id,
        },
    )
    return CalendarSyncState(
        id=sync_id,
        appointment_id=appointment_id,
        provider=provider_normalized,
        sync_direction=sync_direction,
        sync_status="synced",
        external_ref=external_ref,
        conflict_detected=False,
        retry_count=0,
        payload_json=payload or {},
        created_at=created_at,
        updated_at=created_at,
    )


def reconcile_calendar_availability(provider: str, *, doctor_name: str, available_slots: list[str]) -> dict[str, object]:
    normalized_provider = provider.lower()
    checksum_payload = {
        "provider": normalized_provider,
        "doctor_name": doctor_name,
        "available_slots": sorted(available_slots),
    }
    record_workflow_event(
        f"calendar-sync:{normalized_provider}:{doctor_name}",
        trace_id=f"calendar-sync:{normalized_provider}:{doctor_name}",
        correlation_id=doctor_name,
        stage="calendar-sync",
        agent="calendar-integrations",
        action="availability_reconciled",
        decision="accepted",
        confidence=100.0,
        reasons=["availability reconciliation completed"],
        payload=checksum_payload,
    )
    return checksum_payload
