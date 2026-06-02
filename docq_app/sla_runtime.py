from __future__ import annotations

import datetime as dt
import json

from .appointments import fetch_appointments, record_workflow_event
from .db import get_connection
from .human_coordination import enqueue_coordination_item
from .observability import metrics_registry

SLA_THRESHOLDS_MINUTES = {
    "intake_review_latency": 15,
    "scheduling_latency": 20,
    "doctor_response_latency": 60,
    "unresolved_escalation_age": 30,
    "notification_retry_age": 60,
}


def scan_sla_violations(*, worker_id: str = "sla-runtime") -> dict[str, object]:
    now = dt.datetime.now()
    violations = 0
    for appointment in fetch_appointments(limit=400):
        created_at = dt.datetime.fromisoformat(str(appointment["created_at"]))
        age_minutes = int((now - created_at).total_seconds() // 60)
        queue_state = str(appointment["queue_state"])
        status = str(appointment["status"])
        if queue_state in {"manual-review", "assistant-review", "priority-review"} and age_minutes >= SLA_THRESHOLDS_MINUTES["intake_review_latency"]:
            _persist_sla_violation(
                appointment_id=int(appointment["id"]),
                workflow_id=f"appointment-lifecycle:{appointment['id']}",
                sla_type="intake_review_latency",
                threshold_minutes=SLA_THRESHOLDS_MINUTES["intake_review_latency"],
                observed_minutes=age_minutes,
                action_triggered="coordination_queue_escalation",
                evidence={"queue_state": queue_state, "status": status, "worker_id": worker_id, "tenant_key": appointment["tenant_key"]},
            )
            enqueue_coordination_item(
                queue_type="doctor_review",
                appointment_id=int(appointment["id"]),
                workflow_id=f"appointment-lifecycle:{appointment['id']}",
                priority=max(age_minutes, 1),
                queue_status="pending",
                causation_lineage={"sla_type": "intake_review_latency"},
                payload={"status": status},
            )
            violations += 1
        if status == "scheduled" and age_minutes >= SLA_THRESHOLDS_MINUTES["doctor_response_latency"]:
            _persist_sla_violation(
                appointment_id=int(appointment["id"]),
                workflow_id=f"appointment-lifecycle:{appointment['id']}",
                sla_type="doctor_response_latency",
                threshold_minutes=SLA_THRESHOLDS_MINUTES["doctor_response_latency"],
                observed_minutes=age_minutes,
                action_triggered="doctor_review_reminder",
                evidence={"doctor_name": appointment["doctor_name"], "worker_id": worker_id, "tenant_key": appointment["tenant_key"]},
            )
            violations += 1
    metrics_registry.increment("docq_sla_violations_detected_total", float(violations))
    return {"violations_detected": violations}


def sla_summary() -> dict[str, object]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT sla_type, COUNT(*) AS count
            FROM sla_violations
            GROUP BY sla_type
            ORDER BY count DESC
            """
        ).fetchall()
    return {"violations": {str(row["sla_type"]): int(row["count"]) for row in rows}}


def _persist_sla_violation(
    *,
    appointment_id: int,
    workflow_id: str,
    sla_type: str,
    threshold_minutes: int,
    observed_minutes: int,
    action_triggered: str,
    evidence: dict[str, object],
) -> None:
    with get_connection() as connection:
        existing = connection.execute(
            """
            SELECT id FROM sla_violations
            WHERE appointment_id = ? AND sla_type = ? AND violation_status = 'open'
            ORDER BY id DESC LIMIT 1
            """,
            (appointment_id, sla_type),
        ).fetchone()
        if existing is not None:
            return
        created_at = dt.datetime.now().isoformat(timespec="seconds")
        connection.execute(
            """
            INSERT INTO sla_violations (
                appointment_id, tenant_key, workflow_id, sla_type, threshold_minutes, observed_minutes,
                action_triggered, violation_status, evidence_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                appointment_id,
                str(evidence.get("tenant_key") or "default-clinic"),
                workflow_id,
                sla_type,
                threshold_minutes,
                observed_minutes,
                action_triggered,
                "open",
                json.dumps(evidence, sort_keys=True),
                created_at,
            ),
        )
    record_workflow_event(
        workflow_id,
        trace_id=workflow_id,
        correlation_id=str(appointment_id),
        stage="sla-runtime",
        agent="sla-runtime",
        action=f"sla_violation_{sla_type}",
        decision="escalation_required",
        confidence=100.0,
        reasons=[f"{sla_type} exceeded {threshold_minutes} minutes"],
        payload={
            "appointment_id": appointment_id,
            "sla_type": sla_type,
            "threshold_minutes": threshold_minutes,
            "observed_minutes": observed_minutes,
            "action_triggered": action_triggered,
            "evidence": evidence,
        },
    )
