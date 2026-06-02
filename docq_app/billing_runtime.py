from __future__ import annotations

import datetime as dt
import json

from .appointments import record_workflow_event
from .db import get_connection
from .tenancy import get_current_tenant_key


def record_billing_event(
    *,
    appointment_id: int,
    workflow_id: str,
    event_type: str,
    amount_cents: int = 0,
    status: str = "pending",
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    tenant_key = get_current_tenant_key()
    created_at = dt.datetime.now().isoformat(timespec="seconds")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO billing_events (
                tenant_key, appointment_id, workflow_id, event_type, amount_cents, status, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tenant_key, appointment_id, workflow_id, event_type, amount_cents, status, json.dumps(payload or {}, sort_keys=True), created_at),
        )
        billing_id = int(cursor.lastrowid)
    record_workflow_event(
        workflow_id,
        trace_id=workflow_id,
        correlation_id=str(appointment_id),
        stage="billing-runtime",
        agent="billing-runtime",
        action=event_type,
        decision=status,
        confidence=100.0,
        reasons=[f"billing event {event_type} recorded"],
        payload={"billing_event_id": billing_id, "tenant_key": tenant_key, "amount_cents": amount_cents, **(payload or {})},
    )
    return {"id": billing_id, "tenant_key": tenant_key, "status": status}
