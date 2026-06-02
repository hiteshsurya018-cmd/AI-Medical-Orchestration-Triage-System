from __future__ import annotations

import datetime as dt
import json

from .appointments import build_drift_detection_summary, build_model_governance_summary, build_workflow_event_record, build_workflow_model_diff, build_workflow_replay, build_workflow_replay_diff, fetch_appointment_lifecycle_transitions, fetch_appointments, fetch_audit_logs, fetch_automation_runs, fetch_calendar_sync_runs, fetch_care_plans, fetch_coordination_queue_items, fetch_doctor_slots, fetch_emergency_escalations, fetch_latest_clinical_diary, fetch_latest_patient_vitals, fetch_latest_prescription, fetch_latest_workflow_snapshots, fetch_monitoring_checkins, fetch_notifications, fetch_recent_workflow_events, fetch_report_analyses, fetch_sla_violations, fetch_tool_execution_logs, fetch_workflow_lifecycle_stats, fetch_workflow_lineage_summary, fetch_workflow_events, get_patient_history
from .contracts import DriftDetectionSummary, EVENT_SCHEMA_VERSION, GovernanceStateSnapshot, IncidentState, ModelGovernanceSummary, OperationalAlert, QueuePressureSnapshot, RecoveryMetricsSnapshot, ReplayDiff, ReplayIntegrityResult, ShadowPredictionComparison, StuckWorkflowSnapshot, ToolExecutionTelemetry, ToolHealthSnapshot, WorkflowConsoleSnapshot, WorkflowLineageSummary, WorkflowMetricsSummary, WorkflowOperationalIntelligence, WorkflowReplay, WorkflowSlaSnapshot
from .governance_runtime import run_continuous_governance
from .db import get_connection
from .constants import DOCTOR_ACCOUNTS, SPECIALTY_LABELS
from .intelligence_rollups import build_operational_rollup, fetch_latest_rollup
from .notifications import delivery_configs
from .pydantic_compat import model_dump
from .projections import fetch_projection_snapshot
from .runtime_topology import list_runtime_nodes
from .runtime_diagnostics import (
    build_tool_failure_classifications,
    build_tool_latency_profiles,
    classify_failure_signatures,
    classify_workflow_anomalies,
    correlate_incident,
    verify_replay_integrity,
)
from .worker_runtime import build_queue


def build_breakdown(rows, key: str) -> list[dict[str, object]]:
    counter: dict[str, int] = {}
    for row in rows:
        label = row[key]
        counter[label] = counter.get(label, 0) + 1
    max_value = max(counter.values()) if counter else 1
    return [{"label": label, "value": value, "width": round((value / max_value) * 100, 1)} for label, value in sorted(counter.items(), key=lambda item: item[1], reverse=True)]


def build_notification_breakdown(notifications) -> list[dict[str, object]]:
    counter: dict[str, int] = {}
    for item in notifications:
        label = f"{item['channel']} / {item['status']}"
        counter[label] = counter.get(label, 0) + 1
    max_value = max(counter.values()) if counter else 1
    return [{"label": label, "value": value, "width": round((value / max_value) * 100, 1)} for label, value in sorted(counter.items(), key=lambda item: item[1], reverse=True)]


def _appointment_department(appointment) -> str:
    specialty = str(appointment["specialty"] or "General")
    return str(SPECIALTY_LABELS.get(specialty, {}).get("department") or specialty or "General Medicine")


def _priority_label(appointment) -> str:
    urgency = str(appointment["urgency"] or "").strip()
    queue_state = str(appointment["queue_state"] or "").strip()
    status = str(appointment["status"] or "").strip()
    if urgency == "Emergency" or queue_state == "priority-review":
        return "Emergency" if urgency == "Emergency" else "Urgent"
    if urgency == "High" or status == "urgent-review":
        return "Urgent"
    if urgency in {"Medium", "Moderate"}:
        return "Moderate"
    return "Routine"


def build_operations_dashboard_metrics(appointments, notifications) -> dict[str, object]:
    today = dt.date.today().isoformat()
    active_statuses = {"scheduled", "rescheduled", "doctor-acknowledged", "urgent-review", "review"}
    review_states = {"manual-review", "assistant-review", "priority-review"}
    severity_order = {"Emergency": 0, "Urgent": 1, "Moderate": 2, "Routine": 3}
    active_appointments = [item for item in appointments if item["status"] in active_statuses]
    today_appointments = [item for item in appointments if item["appointment_date"] == today]
    emergency_cases = [item for item in appointments if _priority_label(item) == "Emergency"]
    rescheduled_cases = [item for item in appointments if item["status"] == "rescheduled"]
    pending_reviews = [item for item in appointments if item["queue_state"] in review_states or item["status"] in {"review", "urgent-review"}]

    with get_connection() as connection:
        slot_rows = connection.execute(
            """
            SELECT doctor_name, specialty, branch, slot_date, slot_time, status, appointment_id
            FROM doctor_slots
            WHERE slot_date >= ?
            ORDER BY slot_date ASC, slot_time ASC
            """,
            (today,),
        ).fetchall()
        prescription_rows = connection.execute(
            """
            SELECT p.id, p.appointment_id, p.doctor_name, p.patient_name, p.prescription_text, p.status, p.created_at,
                   a.specialty, a.branch
            FROM prescriptions p
            LEFT JOIN appointments a ON a.id = p.appointment_id
            ORDER BY p.created_at DESC
            LIMIT 12
            """
        ).fetchall()

    slots_by_doctor: dict[str, list[object]] = {}
    for slot in slot_rows:
        slots_by_doctor.setdefault(str(slot["doctor_name"]), []).append(slot)

    available_doctors = 0
    busy_doctors = 0
    doctor_activity: list[dict[str, object]] = []
    for doctor in DOCTOR_ACCOUNTS:
        doctor_name = str(doctor["doctor_name"])
        slots = slots_by_doctor.get(doctor_name, [])
        available_slots_today = [slot for slot in slots if slot["slot_date"] == today and slot["status"] == "available"]
        booked_slots_today = [slot for slot in slots if slot["slot_date"] == today and slot["status"] == "booked"]
        queue_count = sum(1 for item in appointments if item["doctor_name"] == doctor_name and item["status"] in active_statuses)
        status = "Available" if available_slots_today else ("Busy" if booked_slots_today or queue_count else "Offline")
        if status == "Available":
            available_doctors += 1
        elif status == "Busy":
            busy_doctors += 1
        doctor_activity.append(
            {
                "doctor_name": doctor_name,
                "display_name": doctor["name"],
                "department": SPECIALTY_LABELS.get(str(doctor["specialty"]), {}).get("department", doctor["specialty"]),
                "specialty": doctor["specialty"],
                "branch": doctor["branch"],
                "status": status,
                "patients_today": sum(1 for item in today_appointments if item["doctor_name"] == doctor_name),
                "average_response_time": "15m" if queue_count else "--",
                "current_queue": queue_count,
            }
        )

    department_names = [
        "Cardiology",
        "Neurology",
        "Orthopedics",
        "Dermatology",
        "ENT",
        "Pediatrics",
        "Gynecology",
        "Psychiatry",
        "General Medicine",
        "Pulmonology",
        "Emergency",
    ]
    department_overview: list[dict[str, object]] = []
    for department in department_names:
        department_appointments = [item for item in active_appointments if _appointment_department(item) == department]
        if department == "Emergency":
            department_appointments = emergency_cases
        department_doctors = [
            doctor for doctor in DOCTOR_ACCOUNTS
            if str(SPECIALTY_LABELS.get(str(doctor["specialty"]), {}).get("department", doctor["specialty"])) == department
        ]
        doctor_names = {str(doctor["doctor_name"]) for doctor in department_doctors}
        next_slot = next(
            (
                f"{slot['slot_date']} {slot['slot_time']}"
                for slot in slot_rows
                if slot["status"] == "available" and (department == "Emergency" or slot["doctor_name"] in doctor_names)
            ),
            "No open slot",
        )
        department_overview.append(
            {
                "department": department,
                "waiting_patients": len(department_appointments),
                "active_doctors": sum(1 for item in doctor_activity if item["doctor_name"] in doctor_names and item["status"] == "Available"),
                "next_available_slot": next_slot,
                "emergency_cases": sum(1 for item in department_appointments if _priority_label(item) == "Emergency"),
            }
        )

    priority_queue = [
        {
            "id": int(item["id"]),
            "patient_name": item["patient_name"],
            "department": _appointment_department(item),
            "doctor_name": item["doctor_name"],
            "risk": _priority_label(item),
            "risk_score": int(item["priority_score"] or 0),
            "appointment_time": f"{item['appointment_date']} {item['slot_time']}",
            "status": item["status"],
        }
        for item in sorted(
            active_appointments,
            key=lambda item: (severity_order.get(_priority_label(item), 9), -(int(item["priority_score"] or 0)), str(item["appointment_date"]), str(item["slot_time"])),
        )[:12]
    ]

    latest_notes = [
        {
            "id": int(item["id"]),
            "patient_name": item["patient_name"],
            "doctor_name": item["doctor_name"],
            "department": _appointment_department(item),
            "doctor_notes": item["doctor_notes"],
            "updated_at": item["acknowledged_at"] or item["created_at"],
        }
        for item in appointments
        if str(item["doctor_notes"] or "").strip()
    ][:8]
    patient_timeline = []
    for item in appointments[:8]:
        patient_timeline.extend(
            [
                {"patient_name": item["patient_name"], "event": "Intake Created", "timestamp": item["created_at"], "department": _appointment_department(item)},
                {"patient_name": item["patient_name"], "event": "Appointment Booked", "timestamp": f"{item['appointment_date']} {item['slot_time']}", "department": _appointment_department(item)},
            ]
        )
        if item["doctor_notes"]:
            patient_timeline.append({"patient_name": item["patient_name"], "event": "Doctor Notes Added", "timestamp": item["acknowledged_at"] or item["created_at"], "department": _appointment_department(item)})

    return {
        "overview": {
            "patients_today": len({item["patient_name"] for item in today_appointments}),
            "active_appointments": len(active_appointments),
            "emergency_cases": len(emergency_cases),
            "doctors_available": available_doctors,
            "doctors_busy": busy_doctors,
            "pending_reviews": len(pending_reviews),
            "rescheduled_appointments": len(rescheduled_cases),
            "queue_load": len(active_appointments) + sum(1 for item in notifications if item["status"] in {"queued", "retry"}),
        },
        "priority_queue": priority_queue,
        "department_overview": department_overview,
        "doctor_activity": doctor_activity,
        "emergency_cases": [item for item in priority_queue if item["risk"] == "Emergency"],
        "rescheduled_cases": [
            {
                "id": int(item["id"]),
                "patient_name": item["patient_name"],
                "doctor_name": item["doctor_name"],
                "appointment_time": f"{item['appointment_date']} {item['slot_time']}",
                "department": _appointment_department(item),
            }
            for item in rescheduled_cases[:10]
        ],
        "prescriptions": [
            {
                "id": int(row["id"]),
                "appointment_id": int(row["appointment_id"]),
                "patient_name": row["patient_name"],
                "doctor_name": row["doctor_name"],
                "department": _appointment_department(row),
                "instructions": row["prescription_text"],
                "status": row["status"],
                "issued_at": row["created_at"],
            }
            for row in prescription_rows
        ],
        "doctor_notes": latest_notes,
        "patient_timeline": patient_timeline[:16],
    }


def _safe_json_loads(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _paginate(items: list[dict[str, object]], *, page: int, page_size: int) -> dict[str, object]:
    total = len(items)
    start = max((page - 1) * page_size, 0)
    end = start + page_size
    return {
        "items": items[start:end],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": max((total + page_size - 1) // page_size, 1),
    }


def _canonical_event_name(action: str, decision: str, payload: dict[str, object]) -> str:
    normalized_action = str(action or "").strip().lower()
    normalized_decision = str(decision or "").strip().lower()
    if "notification_sent" in normalized_action or normalized_decision == "sent":
        return "reminder_sent" if "reminder" in normalized_action or "appointment_reminder" in str(payload.get("message_category", "")) else "whatsapp_dispatched"
    if "notification_retry" in normalized_action or normalized_decision == "retry":
        return "retry_scheduled"
    if "notification_failed" in normalized_action or normalized_decision == "failed":
        return "notification_failed"
    if "intake" in normalized_action:
        return "intake_created"
    if "appointment" in normalized_action and "created" in normalized_action:
        return "appointment_created"
    if "reminder" in normalized_action and "queued" in normalized_action:
        return "reminder_scheduled"
    if "escalat" in normalized_action or normalized_decision == "emergency_escalation":
        return "escalation_triggered"
    if "cancel" in normalized_action:
        return "patient_cancelled"
    if "confirm" in normalized_action:
        return "patient_confirmed"
    if "signup" in normalized_action or "patient" in normalized_action and "promot" in normalized_action:
        return "patient_promoted"
    return normalized_action or normalized_decision or "workflow_event"


def build_admin_event_feed(*, workflow_id: str = "", event_type: str = "", page: int = 1, page_size: int = 25) -> dict[str, object]:
    rows = fetch_workflow_events(workflow_id, limit=max(page * page_size, 50)) if workflow_id else fetch_recent_workflow_events(limit=max(page * page_size, 50))
    items: list[dict[str, object]] = []
    for row in rows:
        record = model_dump(build_workflow_event_record(row))
        canonical_event = _canonical_event_name(str(record.get("action", "")), str(record.get("decision", "")), dict(record.get("payload", {})))
        item = {
            "event_id": record["event_id"],
            "workflow_id": record["workflow_id"],
            "trace_id": record["trace_id"],
            "root_event_id": record["root_event_id"],
            "causation_id": record["causation_id"],
            "timestamp": record["timestamp"],
            "type": record["type"],
            "severity": record["severity"],
            "state": record["state"],
            "agent": record["agent"],
            "action": record["action"],
            "decision": record["decision"],
            "canonical_event": canonical_event,
            "reasons": record["reasons"],
            "payload": record["payload"],
        }
        if event_type and canonical_event != event_type:
            continue
        items.append(item)
    return _paginate(items, page=page, page_size=page_size)


def build_admin_workflow_feed(*, page: int = 1, page_size: int = 20, decision: str = "", state: str = "") -> dict[str, object]:
    items: list[dict[str, object]] = []
    for row in fetch_latest_workflow_snapshots(limit=max(page * page_size, 100)):
        entry = {
            "workflow_id": str(row["workflow_id"]),
            "decision": str(row["decision"] or "pending"),
            "stage": str(row["stage"] or "unknown"),
            "agent": str(row["agent"] or ""),
            "confidence": float(row["confidence"] or 0.0),
            "created_at": str(row["created_at"]),
            "tenant_key": str(row["tenant_key"] or "default-clinic"),
        }
        if decision and entry["decision"] != decision:
            continue
        if state and entry["stage"] != state:
            continue
        items.append(entry)
    return _paginate(items, page=page, page_size=page_size)


def build_admin_notification_feed(*, page: int = 1, page_size: int = 25, status: str = "", channel: str = "") -> dict[str, object]:
    items: list[dict[str, object]] = []
    for row in fetch_notifications(limit=max(page * page_size, 100)):
        metadata = _safe_json_loads(row["provider_metadata_json"])
        item = {
            "id": int(row["id"]),
            "appointment_id": row["appointment_id"],
            "tenant_key": str(row["tenant_key"] or "default-clinic"),
            "target_type": str(row["target_type"]),
            "target_name": str(row["target_name"]),
            "channel": str(row["channel"]),
            "message": str(row["message"]),
            "status": str(row["status"]),
            "twilio_sid": str(row["external_id"] or metadata.get("external_id") or ""),
            "delivery_state": str(row["status"]),
            "acknowledged_at": str(row["acknowledged_at"] or ""),
            "retry_count": int(row["attempt_count"] or 0),
            "failure_reason": str(row["last_error"] or metadata.get("last_error") or ""),
            "correlation_id": str(row["correlation_id"] or ""),
            "provider_latency_ms": metadata.get("latency_ms", 0),
            "message_category": str(row["message_category"] or ""),
            "created_at": str(row["created_at"]),
        }
        if status and item["status"] != status:
            continue
        if channel and item["channel"] != channel:
            continue
        items.append(item)
    return _paginate(items, page=page, page_size=page_size)


def build_queue_runtime_snapshot(config: dict[str, object]) -> dict[str, object]:
    with get_connection() as connection:
        counts = connection.execute(
            """
            SELECT execution_state, COUNT(*) AS count
            FROM worker_execution_ledger
            GROUP BY execution_state
            """
        ).fetchall()
        pending_notifications = int(connection.execute("SELECT COUNT(*) FROM notifications WHERE status IN ('queued', 'retry')").fetchone()[0])
        failed_notifications = int(connection.execute("SELECT COUNT(*) FROM notifications WHERE status = 'failed'").fetchone()[0])
    counts_by_state = {str(row["execution_state"]): int(row["count"]) for row in counts}
    queue_depth = counts_by_state.get("queued", 0) + pending_notifications
    snapshot = {
        "queue_name": "docq-default",
        "queue_depth": queue_depth,
        "active_jobs": counts_by_state.get("running", 0) + counts_by_state.get("started", 0),
        "failed_jobs": counts_by_state.get("failed", 0) + failed_notifications,
        "retry_jobs": counts_by_state.get("retry", 0) + pending_notifications,
        "scheduled_jobs": pending_notifications,
        "dispatch_latency_ms": 0,
        "redis_connected": False,
    }
    redis_url = str(config.get("REDIS_URL", "") or "")
    if redis_url:
        try:
            queue = build_queue(redis_url)
            if queue is not None:
                scheduled_count = int(queue.scheduled_job_registry.count) if hasattr(queue, "scheduled_job_registry") else 0
                deferred_count = int(queue.deferred_job_registry.count) if hasattr(queue, "deferred_job_registry") else 0
                snapshot.update(
                    {
                        "queue_depth": int(queue.count),
                        "active_jobs": int(queue.started_job_registry.count),
                        "failed_jobs": int(queue.failed_job_registry.count),
                        "retry_jobs": scheduled_count,
                        "scheduled_jobs": deferred_count,
                        "redis_connected": True,
                    }
                )
        except Exception:
            snapshot["redis_connected"] = False
    return snapshot


def build_worker_runtime_snapshot() -> dict[str, object]:
    nodes = list_runtime_nodes()
    now = dt.datetime.now()
    with get_connection() as connection:
        ledger_rows = connection.execute(
            """
            SELECT owner_worker_id, execution_state, COUNT(*) AS count, MAX(updated_at) AS latest_update
            FROM worker_execution_ledger
            GROUP BY owner_worker_id, execution_state
            ORDER BY latest_update DESC
            """
        ).fetchall()
        consumer_rows = connection.execute(
            """
            SELECT consumer_id, node_id, stream_subject, ownership_generation, checkpoint_outbox_id, updated_at
            FROM consumer_ownership
            ORDER BY updated_at DESC
            """
        ).fetchall()
    workers: dict[str, dict[str, object]] = {}
    for row in ledger_rows:
        worker_id = str(row["owner_worker_id"] or "unassigned")
        worker = workers.setdefault(worker_id, {"worker_id": worker_id, "states": {}, "latest_update": str(row["latest_update"] or ""), "heartbeat": "", "stale": False})
        worker["states"][str(row["execution_state"])] = int(row["count"])
    node_by_id = {str(node["node_id"]): node for node in nodes}
    for worker in workers.values():
        node = node_by_id.get(worker["worker_id"])
        heartbeat = str(node["heartbeat_at"]) if node else ""
        worker["heartbeat"] = heartbeat
        parsed = _parse_iso(heartbeat)
        worker["stale"] = bool(parsed and (now - parsed).total_seconds() > 180)
    return {
        "nodes": nodes,
        "workers": list(workers.values()),
        "consumers": [dict(row) for row in consumer_rows],
        "healthy_workers": sum(1 for worker in workers.values() if not worker["stale"]),
        "stale_workers": sum(1 for worker in workers.values() if worker["stale"]),
    }


def build_incident_console_snapshot(config: dict[str, object]) -> dict[str, object]:
    workflow_metrics = build_workflow_metrics()
    intelligence = build_operational_intelligence(config, workflow_metrics, slot_utilization())
    notifications = build_admin_notification_feed(page=1, page_size=20)
    failed = [item for item in notifications["items"] if item["status"] == "failed"]
    retrying = [item for item in notifications["items"] if item["status"] == "retry"]
    return {
        "incident_state": intelligence["incident_state"],
        "alerts": intelligence["alerts"],
        "stuck_workflows": intelligence["stuck_workflows"],
        "failed_notifications": failed,
        "retry_notifications": retrying,
        "sla_violations": [dict(row) for row in fetch_sla_violations(limit=12)],
        "coordination_items": [dict(row) for row in fetch_coordination_queue_items(limit=12)],
    }


def build_patient_continuity_snapshot(limit: int = 12) -> dict[str, object]:
    with get_connection() as connection:
        linked_count = int(connection.execute("SELECT COUNT(*) FROM patient_profiles WHERE linked_user_id IS NOT NULL").fetchone()[0])
        recent = connection.execute(
            """
            SELECT patient_name, patient_email, phone, linked_user_id, last_visit_at, updated_at, communication_preferences_json
            FROM patient_profiles
            WHERE linked_user_id IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items = []
    for row in recent:
        prefs = _safe_json_loads(row["communication_preferences_json"])
        items.append(
            {
                "patient_name": str(row["patient_name"]),
                "patient_email": str(row["patient_email"] or ""),
                "phone": str(row["phone"] or ""),
                "linked_user_id": int(row["linked_user_id"] or 0),
                "last_visit_at": str(row["last_visit_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
                "whatsapp_enabled": bool(prefs.get("whatsapp")),
            }
        )
    return {"linked_patient_count": linked_count, "items": items}


def build_schedule_governance_snapshot(limit: int = 24) -> dict[str, object]:
    with get_connection() as connection:
        slots = connection.execute(
            """
            SELECT id, doctor_name, specialty, branch, slot_date, slot_time, status, appointment_id
            FROM doctor_slots
            WHERE slot_date >= ?
            ORDER BY slot_date ASC, slot_time ASC
            LIMIT ?
            """,
            (dt.date.today().isoformat(), limit),
        ).fetchall()
    appointments = fetch_appointments()
    slot_map: dict[tuple[str, str, str], int] = {}
    for item in appointments:
        key = (str(item["doctor_name"]), str(item["appointment_date"]), str(item["slot_time"] or ""))
        slot_map[key] = slot_map.get(key, 0) + 1
    conflicts = [{"doctor_name": doctor, "appointment_date": date, "slot_time": slot_time, "count": count} for (doctor, date, slot_time), count in slot_map.items() if slot_time and count > 1]
    return {
        "slots": [dict(row) for row in slots[:limit]],
        "conflicts": conflicts[:12],
        "booked_slots": sum(1 for row in slots if str(row["status"]) == "booked"),
        "available_slots": sum(1 for row in slots if str(row["status"]) == "available"),
    }


def build_admin_runtime_snapshot(config: dict[str, object]) -> dict[str, object]:
    queue = build_queue_runtime_snapshot(config)
    workers = build_worker_runtime_snapshot()
    delivery = delivery_configs(config)
    return {
        "replay_authority": "healthy",
        "queue": queue,
        "workers": workers,
        "twilio": {"configured": bool(delivery.get("sms") or delivery.get("whatsapp")), "whatsapp_ready": bool(delivery.get("whatsapp"))},
        "redis": {"connected": queue["redis_connected"]},
        "nats": {"configured": bool(config.get("NATS_URL")), "backend": str(config.get("EVENT_BUS_BACKEND", "inprocess"))},
        "database": {"configured": bool(config.get("DATABASE_URL"))},
    }


def slot_utilization() -> dict[str, int]:
    with get_connection() as connection:
        total = connection.execute("SELECT COUNT(*) FROM doctor_slots").fetchone()[0]
        booked = connection.execute("SELECT COUNT(*) FROM doctor_slots WHERE status = 'booked'").fetchone()[0]
    return {"total": total, "booked": booked, "available": max(total - booked, 0)}


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def _minutes_since(value: str | None, now: dt.datetime) -> int:
    parsed = _parse_iso(value)
    if parsed is None:
        return 0
    return max(int((now - parsed).total_seconds() // 60), 0)


def _minutes_between(start: str | None, end: str | None) -> float:
    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    if start_dt is None or end_dt is None:
        return 0.0
    return max(round((end_dt - start_dt).total_seconds() / 60, 1), 0.0)


def build_workflow_metrics() -> dict[str, object]:
    snapshots = fetch_latest_workflow_snapshots(limit=200)
    recent_events = fetch_recent_workflow_events(limit=18)
    decision_counter: dict[str, int] = {}
    confidence_values: list[float] = []
    active_workflows = 0
    human_review = 0
    emergency = 0
    autonomous = 0
    failed_recoveries = 0

    for row in snapshots:
        decision = str(row["decision"] or "unknown")
        decision_counter[decision] = decision_counter.get(decision, 0) + 1
        if row["confidence"] is not None:
            confidence_values.append(float(row["confidence"]))
        if row["stage"] not in {"completed", "failed"}:
            active_workflows += 1
        if decision == "human_review":
            human_review += 1
        elif decision == "emergency_escalation":
            emergency += 1
        elif decision == "autonomous_booking":
            autonomous += 1

    for row in recent_events:
        if "fallback" in str(row["action"]).lower() or "failed" in str(row["decision"]).lower():
            failed_recoveries += 1

    max_decision = max(decision_counter.values()) if decision_counter else 1
    decision_breakdown = [
        {
            "label": label,
            "value": value,
            "width": round((value / max_decision) * 100, 1),
        }
        for label, value in sorted(decision_counter.items(), key=lambda item: item[1], reverse=True)
    ]
    average_confidence = round(sum(confidence_values) / len(confidence_values), 1) if confidence_values else 0.0

    activity_feed = [build_workflow_event_record(row) for row in recent_events]
    summary = WorkflowMetricsSummary(
        active_workflows=active_workflows,
        human_review_queue=human_review,
        emergency_escalations=emergency,
        autonomous_bookings=autonomous,
        failed_recoveries=failed_recoveries,
        average_confidence=average_confidence,
        decision_breakdown=decision_breakdown,
        activity_feed=activity_feed,
    )
    return model_dump(summary)


def build_operational_intelligence(
    config: dict[str, object],
    workflow_metrics: dict[str, object],
    utilization: dict[str, int],
    *,
    replay_integrity: dict[str, object] | None = None,
    model_governance: dict[str, object] | None = None,
    governance_state: dict[str, object] | None = None,
) -> dict[str, object]:
    now = dt.datetime.now()
    snapshots = fetch_latest_workflow_snapshots(limit=200)
    lifecycle = {row["workflow_id"]: row for row in fetch_workflow_lifecycle_stats(limit=200)}
    notifications = fetch_notifications(limit=200)
    tool_logs = fetch_tool_execution_logs(limit=400)
    lineage_rows = fetch_workflow_lineage_summary(limit=10)
    active_workflows = int(workflow_metrics.get("active_workflows", 0) or 0)
    human_review_queue = int(workflow_metrics.get("human_review_queue", 0) or 0)
    emergency_escalations = int(workflow_metrics.get("emergency_escalations", 0) or 0)
    failed_recoveries = int(workflow_metrics.get("failed_recoveries", 0) or 0)
    unresolved_workflows = sum(1 for row in snapshots if row["stage"] not in {"completed", "failed"})
    denominator = max(active_workflows, 1)
    review_pressure_pct = round((human_review_queue / denominator) * 100, 1)
    emergency_pressure_pct = round((emergency_escalations / denominator) * 100, 1)
    retry_pressure_pct = round((failed_recoveries / denominator) * 100, 1)

    if emergency_pressure_pct >= 35 or review_pressure_pct >= 75:
        pressure_level = "critical"
    elif emergency_pressure_pct >= 20 or review_pressure_pct >= 55 or retry_pressure_pct >= 30:
        pressure_level = "elevated"
    elif review_pressure_pct >= 35 or retry_pressure_pct >= 15:
        pressure_level = "watch"
    else:
        pressure_level = "stable"

    queue_pressure = QueuePressureSnapshot(
        review_pressure_pct=review_pressure_pct,
        emergency_pressure_pct=emergency_pressure_pct,
        retry_pressure_pct=retry_pressure_pct,
        unresolved_workflows=unresolved_workflows,
        pressure_level=pressure_level,
    )

    stuck_workflows: list[StuckWorkflowSnapshot] = []
    workflow_ages: list[float] = []
    review_ages: list[int] = []
    resolution_minutes: list[float] = []

    for row in snapshots:
        workflow_id = str(row["workflow_id"])
        lifecycle_row = lifecycle.get(workflow_id)
        latest_at = row["created_at"]
        minutes_stalled = _minutes_since(latest_at, now)
        if row["stage"] not in {"completed", "failed"}:
            workflow_ages.append(float(minutes_stalled))
        if str(row["decision"]) == "human_review":
            review_ages.append(minutes_stalled)
        if lifecycle_row:
            duration = _minutes_between(lifecycle_row["started_at"], lifecycle_row["latest_at"])
            if row["stage"] == "completed":
                resolution_minutes.append(duration)
        threshold = 15 if str(row["decision"]) == "human_review" else 10
        if row["stage"] not in {"completed", "failed"} and minutes_stalled >= threshold:
            severity = "critical" if str(row["decision"]) == "emergency_escalation" or minutes_stalled >= 30 else "warning"
            stuck_workflows.append(
                StuckWorkflowSnapshot(
                    workflow_id=workflow_id,
                    state=str(row["stage"]),
                    decision=str(row["decision"] or "pending"),
                    minutes_stalled=minutes_stalled,
                    severity=severity,
                )
            )
    stuck_workflows = sorted(stuck_workflows, key=lambda item: item.minutes_stalled, reverse=True)[:6]

    retrying_notifications = sum(1 for item in notifications if item["status"] == "retry")
    failed_notifications = sum(1 for item in notifications if item["status"] == "failed")
    fallback_events = sum(1 for item in notifications if item["last_error"] and "fallback-from-" in str(item["last_error"]))
    sent_notifications = sum(1 for item in notifications if item["status"] in {"sent", "visible"})
    attempted_notifications = retrying_notifications + failed_notifications + sent_notifications
    recovery_success_rate = round((sent_notifications / attempted_notifications) * 100, 1) if attempted_notifications else 100.0
    recovery_metrics = RecoveryMetricsSnapshot(
        retrying_notifications=retrying_notifications,
        failed_notifications=failed_notifications,
        fallback_events=fallback_events,
        sent_notifications=sent_notifications,
        recovery_success_rate=recovery_success_rate,
    )

    delivery = delivery_configs(config)
    slot_utilization_pct = round((utilization["booked"] / max(utilization["total"], 1)) * 100, 1) if utilization["total"] else 0.0
    tool_groups: dict[str, list] = {}
    for row in tool_logs:
        tool_groups.setdefault(str(row["tool_name"]), []).append(row)
    telemetry_models = [
        ToolExecutionTelemetry(
            invocation_id=str(row["invocation_id"]),
            workflow_id=str(row["workflow_id"]),
            trace_id=str(row["trace_id"]),
            tool_name=str(row["tool_name"]),
            agent=str(row["agent"]),
            parent_event_id=row["parent_event_id"],
            replay_branch_id=str(row["replay_branch_id"] or "main"),
            latency_ms=int(row["latency_ms"]),
            success=bool(row["success"]),
            fallback_used=bool(row["fallback_used"]),
            error=str(row["error"]) if row["error"] else None,
            created_at=str(row["created_at"]),
        )
        for row in tool_logs
    ]

    tool_health: list[ToolHealthSnapshot] = []
    for tool_name, rows in sorted(tool_groups.items()):
        successes = sum(1 for row in rows if int(row["success"]) == 1)
        total = len(rows)
        avg_latency = round(sum(int(row["latency_ms"]) for row in rows) / max(total, 1), 1)
        success_rate = round((successes / max(total, 1)) * 100, 1)
        status = "degraded" if success_rate < 90 else ("watch" if success_rate < 100 or avg_latency > 250 else "healthy")
        tool_health.append(
            ToolHealthSnapshot(
                name=tool_name,
                status=status,
                metric_label="Success / latency",
                metric_value=f"{success_rate}% / {avg_latency}ms",
                detail=f"{total} invocations recorded from canonical tool telemetry",
            )
        )

    if not tool_health:
        tool_health.append(
            ToolHealthSnapshot(
                name="tool_runtime",
                status="watch",
                metric_label="Telemetry coverage",
                metric_value="0 invocations",
                detail="No direct tool telemetry has been recorded yet in the current environment.",
            )
        )

    tool_health.extend(
        [
            ToolHealthSnapshot(
                name="notification_delivery",
                status="degraded" if failed_notifications > 0 else ("watch" if retrying_notifications > 0 else "healthy"),
                metric_label="Recovery success",
                metric_value=f"{recovery_success_rate}%",
                detail=f"{retrying_notifications} retrying, {failed_notifications} failed, {fallback_events} fallbacks",
            ),
            ToolHealthSnapshot(
                name="notification_config",
                status="healthy" if all(delivery.values()) else "watch",
                metric_label="Configured channels",
                metric_value=f"{sum(1 for ready in delivery.values() if ready)}/{len(delivery)}",
                detail="SMS, WhatsApp, and email readiness derived from current environment config",
            ),
            ToolHealthSnapshot(
                name="slot_allocator_capacity",
                status="watch" if utilization["available"] <= 5 else "healthy",
                metric_label="Slot availability",
                metric_value=f"{utilization['available']}/{utilization['total']}",
                detail=f"{slot_utilization_pct}% capacity booked across the live slot pool",
            ),
        ]
    )
    latency_profiles = build_tool_latency_profiles(telemetry_models)
    failure_classifications = build_tool_failure_classifications(telemetry_models)

    workflow_profiles = []
    for row in lineage_rows:
        lifecycle_row = lifecycle.get(row["workflow_id"])
        duration_minutes = _minutes_between(lifecycle_row["started_at"], lifecycle_row["latest_at"]) if lifecycle_row else 0.0
        retry_count = sum(1 for item in snapshots if item["workflow_id"] == row["workflow_id"] and "retry" in str(item["action"]).lower())
        tool_latencies = [model.latency_ms for model in telemetry_models if model.workflow_id == row["workflow_id"]]
        workflow_profiles.append(
            {
                "workflow_id": str(row["workflow_id"]),
                "correlation_id": str(row["correlation_id"] or row["workflow_id"]),
                "duration_minutes": duration_minutes,
                "retry_count": float(retry_count),
                "latency_ms": float(sum(tool_latencies) / len(tool_latencies)) if tool_latencies else 0.0,
            }
        )
    duration_baseline = round(sum(float(item["duration_minutes"]) for item in workflow_profiles) / len(workflow_profiles), 1) if workflow_profiles else 0.0
    retry_baseline = round(sum(float(item["retry_count"]) for item in workflow_profiles) / len(workflow_profiles), 1) if workflow_profiles else 0.0
    latency_baseline = round(sum(float(item["latency_ms"]) for item in workflow_profiles) / len(workflow_profiles), 1) if workflow_profiles else 0.0
    anomalies = classify_workflow_anomalies(
        workflow_profiles,
        baseline_duration=duration_baseline,
        baseline_retry=retry_baseline,
        baseline_latency=latency_baseline,
    )

    sla_metrics = WorkflowSlaSnapshot(
        avg_resolution_minutes=round(sum(resolution_minutes) / len(resolution_minutes), 1) if resolution_minutes else 0.0,
        avg_workflow_age_minutes=round(sum(workflow_ages) / len(workflow_ages), 1) if workflow_ages else 0.0,
        avg_review_age_minutes=round(sum(review_ages) / len(review_ages), 1) if review_ages else 0.0,
    )

    alerts: list[OperationalAlert] = []
    if pressure_level in {"critical", "elevated"}:
        alerts.append(
            OperationalAlert(
                severity="critical" if pressure_level == "critical" else "warning",
                message=f"Workflow queue pressure is {pressure_level}. Review queue at {review_pressure_pct}% and emergency load at {emergency_pressure_pct}%.",
            )
        )
    if stuck_workflows:
        top = stuck_workflows[0]
        alerts.append(
            OperationalAlert(
                severity=top.severity,
                message=f"{top.workflow_id} stalled in {top.state} for {top.minutes_stalled} minutes.",
            )
        )
    if failed_notifications > 0 or retrying_notifications >= 3:
        alerts.append(
            OperationalAlert(
                severity="warning",
                message=f"Notification recovery pressure detected with {retrying_notifications} retries and {failed_notifications} failed deliveries.",
            )
        )

    if pressure_level == "critical":
        incident_state = IncidentState(
            active=True,
            level="critical",
            title="Incident Mode Active",
            summary="Workflow congestion and escalation pressure require operator attention.",
            triggers=[alert.message for alert in alerts] or ["Critical queue pressure detected."],
        )
    elif pressure_level == "elevated" or failed_notifications > 0 or stuck_workflows:
        incident_state = IncidentState(
            active=True,
            level="elevated",
            title="Degraded Operations",
            summary="The orchestration layer is showing signs of coordination strain.",
            triggers=[alert.message for alert in alerts] or ["Elevated workflow pressure detected."],
        )
    elif pressure_level == "watch" or retrying_notifications > 0:
        incident_state = IncidentState(
            active=False,
            level="watch",
            title="Watch State",
            summary="The platform is stable but trending toward higher retry or review pressure.",
            triggers=[alert.message for alert in alerts],
        )
    else:
        incident_state = IncidentState(
            active=False,
            level="stable",
            title="Nominal",
            summary="Workflow routing, recovery, and queue behavior are within expected thresholds.",
            triggers=[],
        )

    lineage_summaries = [
        WorkflowLineageSummary(
            workflow_id=row["workflow_id"],
            root_event_id=row["root_event_id"],
            latest_event_id=row["latest_event_id"],
            event_count=row["event_count"],
            tool_invocation_count=row["tool_invocation_count"] or 0,
            last_tool_name=str(row["last_tool_name"] or ""),
            correlation_id=str(row["correlation_id"] or row["workflow_id"]),
        )
        for row in lineage_rows
    ]

    signatures = classify_failure_signatures(
        review_pressure_pct=review_pressure_pct,
        emergency_pressure_pct=emergency_pressure_pct,
        retry_pressure_pct=retry_pressure_pct,
        recovery_success_rate=recovery_success_rate,
        failed_notifications=failed_notifications,
        retrying_notifications=retrying_notifications,
        tool_health=tool_health,
        anomalies=anomalies,
        incident_level=incident_state.level,
    )
    incident_correlation = correlate_incident(
        incident_level=incident_state.level,
        degraded_tools=[tool.name for tool in tool_health if tool.status == "degraded"],
        failed_notifications=failed_notifications,
        retrying_notifications=retrying_notifications,
        review_pressure_pct=review_pressure_pct,
        emergency_pressure_pct=emergency_pressure_pct,
        anomalies=anomalies,
    )
    if replay_integrity and replay_integrity.get("divergence_detected"):
        alerts.append(
            OperationalAlert(
                severity="critical",
                message=f"Replay integrity mismatch detected for {replay_integrity.get('workflow_id', 'selected workflow')}.",
            )
        )

    lifecycle_projection = fetch_projection_snapshot("lifecycle_projection")
    reminder_projection = fetch_projection_snapshot("reminder_projection")
    sla_projection = fetch_projection_snapshot("sla_projection")
    coordination_projection = fetch_projection_snapshot("coordination_projection")
    reassignment_projection = fetch_projection_snapshot("reassignment_projection")
    incident_workflow_projection = fetch_projection_snapshot("incident_workflow_projection")
    calendar_sync_projection = fetch_projection_snapshot("calendar_sync_projection")
    coordination_items = fetch_coordination_queue_items(limit=200)
    sla_violations = fetch_sla_violations(limit=200)
    calendar_runs = fetch_calendar_sync_runs(limit=200)
    recent_transition_count = 0
    if lifecycle_projection:
        any_appointment_id = next(iter(lifecycle_projection.keys()))
        try:
            recent_transition_count = len(fetch_appointment_lifecycle_transitions(int(any_appointment_id), limit=20))
        except (TypeError, ValueError):
            recent_transition_count = 0

    intelligence = WorkflowOperationalIntelligence(
        version=EVENT_SCHEMA_VERSION,
        incident_state=incident_state,
        incident_correlation=incident_correlation,
        queue_pressure=queue_pressure,
        stuck_workflows=stuck_workflows,
        lineage_summaries=lineage_summaries,
        recovery_metrics=recovery_metrics,
        tool_health=tool_health,
        tool_latency_profiles=latency_profiles,
        tool_failure_classifications=failure_classifications,
        failure_signatures=signatures.signatures,
        anomalies=anomalies,
        sla_metrics=sla_metrics,
        alerts=alerts,
        model_governance=ModelGovernanceSummary(**model_governance) if model_governance else None,
        governance_state=GovernanceStateSnapshot(**governance_state) if governance_state else None,
        lifecycle_summary={"active_states": len(lifecycle_projection), "recent_transition_count": recent_transition_count, "projection": lifecycle_projection},
        reminder_summary={"pending": sum(1 for item in notifications if item["status"] in {"queued", "retry"}), "projection": reminder_projection},
        sla_summary={"open_violations": len(sla_violations), "projection": sla_projection},
        coordination_summary={
            "queue_items": len(coordination_items),
            "projection": coordination_projection,
            "reassignments": reassignment_projection,
            "incident_workflows": incident_workflow_projection,
        },
        calendar_sync_summary={"runs": len(calendar_runs), "projection": calendar_sync_projection},
    )
    return model_dump(intelligence)


def build_dashboard_metrics(config: dict[str, object], workflow_id: str = "", compare_workflow_id: str = "") -> dict[str, object]:
    appointments = fetch_appointments()
    notifications = fetch_notifications()
    today = dt.date.today().isoformat()
    utilization = slot_utilization()
    workflow_metrics = build_workflow_metrics()
    replay = build_workflow_replay(workflow_id, limit=60) if workflow_id else None
    workflow_model_diff = build_workflow_model_diff(workflow_id) if workflow_id else None
    workflow_drift = build_drift_detection_summary()
    model_governance = build_model_governance_summary(workflow_id=workflow_id)
    governance_state = run_continuous_governance(refresh=False)
    latest_rollup = fetch_latest_rollup("operational")
    if latest_rollup is None:
        try:
            latest_rollup = build_operational_rollup()
        except Exception:
            latest_rollup = None
    workflow_projection = fetch_projection_snapshot("workflow_projection")
    governance_projection = fetch_projection_snapshot("governance_projection")
    replay_projection = fetch_projection_snapshot("replay_projection")
    lifecycle_projection = fetch_projection_snapshot("lifecycle_projection")
    reminder_projection = fetch_projection_snapshot("reminder_projection")
    sla_projection = fetch_projection_snapshot("sla_projection")
    coordination_projection = fetch_projection_snapshot("coordination_projection")
    calendar_sync_projection = fetch_projection_snapshot("calendar_sync_projection")
    replay_integrity = None
    if workflow_id and replay:
        replay_model = WorkflowReplay(**replay)
        replay_integrity = model_dump(verify_replay_integrity(workflow_id, replay_model, WorkflowReplay(**build_workflow_replay(workflow_id, limit=60))))
    admin_runtime = build_admin_runtime_snapshot(config)
    admin_events = build_admin_event_feed(page=1, page_size=16)
    incident_console = build_incident_console_snapshot(config)
    patient_continuity = build_patient_continuity_snapshot(limit=8)
    schedule_governance = build_schedule_governance_snapshot(limit=16)
    operational_intelligence = build_operational_intelligence(
        config,
        workflow_metrics,
        utilization,
        replay_integrity=replay_integrity,
        model_governance=model_governance,
        governance_state=governance_state,
    )
    workflow_diff = build_workflow_replay_diff(workflow_id, compare_workflow_id, limit=60) if workflow_id and compare_workflow_id else None
    operations = build_operations_dashboard_metrics(appointments, notifications)
    return {
        "total_appointments": len(appointments),
        "today_appointments": sum(1 for item in appointments if item["appointment_date"] == today),
        "high_priority_cases": sum(1 for item in appointments if item["urgency"] in {"High", "Emergency"}),
        "automated_bookings": sum(1 for item in appointments if item["queue_state"] == "awaiting-doctor"),
        "review_cases": sum(1 for item in appointments if item["queue_state"] in {"manual-review", "assistant-review", "priority-review"}),
        "top_specialty": build_breakdown(appointments, "specialty")[0]["label"] if appointments else "General",
        "recent_appointments": appointments[:12],
        "notifications": notifications,
        "specialty_breakdown": build_breakdown(appointments, "specialty"),
        "queue_breakdown": build_breakdown(appointments, "queue_state"),
        "doctor_breakdown": build_breakdown(appointments, "doctor_name"),
        "branch_breakdown": build_breakdown(appointments, "branch"),
        "reception_breakdown": build_breakdown(appointments, "created_by") if appointments else [],
        "notification_breakdown": build_notification_breakdown(notifications),
        "audit_logs": fetch_audit_logs(),
        "automation_runs": fetch_automation_runs(),
        "delivery_health": delivery_configs(config),
        "slot_utilization": utilization,
        "automation_ready": sum(1 for item in notifications if item["status"] in {"sent", "visible"}),
        "workflow_metrics": workflow_metrics,
        "operational_intelligence": operational_intelligence,
        "workflow_replay": replay,
        "workflow_diff": workflow_diff,
        "workflow_model_diff": workflow_model_diff,
        "workflow_drift": workflow_drift,
        "model_governance": model_governance,
        "governance_state": governance_state,
        "rollup_summary": latest_rollup,
        "workflow_projection": workflow_projection,
        "governance_projection": governance_projection,
        "replay_projection": replay_projection,
        "lifecycle_projection": lifecycle_projection,
        "reminder_projection": reminder_projection,
        "sla_projection": sla_projection,
        "coordination_projection": coordination_projection,
        "calendar_sync_projection": calendar_sync_projection,
        "replay_integrity": replay_integrity,
        "selected_workflow_id": workflow_id,
        "compare_workflow_id": compare_workflow_id,
        "admin_runtime": admin_runtime,
        "admin_events": admin_events,
        "incident_console": incident_console,
        "patient_continuity": patient_continuity,
        "schedule_governance": schedule_governance,
        "operations": operations,
    }


def build_workflow_console_snapshot(config: dict[str, object], workflow_id: str = "", compare_workflow_id: str = "") -> dict[str, object]:
    metrics = build_dashboard_metrics(config, workflow_id=workflow_id, compare_workflow_id=compare_workflow_id)
    snapshot = WorkflowConsoleSnapshot(
        workflow_metrics=WorkflowMetricsSummary(**metrics["workflow_metrics"]),
        operational_intelligence=WorkflowOperationalIntelligence(**metrics["operational_intelligence"]),
        workflow_replay=metrics["workflow_replay"],
        workflow_diff=ReplayDiff(**metrics["workflow_diff"]) if metrics["workflow_diff"] else None,
        workflow_model_diff=ShadowPredictionComparison(**metrics["workflow_model_diff"]) if metrics["workflow_model_diff"] else None,
        workflow_drift=DriftDetectionSummary(**metrics["workflow_drift"]) if metrics["workflow_drift"] else None,
        replay_integrity=ReplayIntegrityResult(**metrics["replay_integrity"]) if metrics["replay_integrity"] else None,
        selected_workflow_id=str(metrics["selected_workflow_id"]),
        generated_at=dt.datetime.now().isoformat(timespec="seconds"),
    )
    return model_dump(snapshot)


def build_doctor_metrics(doctor_name: str) -> dict[str, object]:
    appointments = fetch_appointments(doctor_name=doctor_name)
    notifications = fetch_notifications(target_name=doctor_name)
    slots = fetch_doctor_slots(doctor_name)
    pending = [item for item in appointments if item["queue_state"] in {"awaiting-doctor", "assistant-review", "priority-review", "manual-review"} and not item["acknowledged_at"]]
    urgency_rank = {"Emergency": 0, "High": 1, "Moderate": 2, "Low": 3}
    pending = sorted(
        pending,
        key=lambda item: (
            urgency_rank.get(str(item["urgency"]), 4),
            -float(item["priority_score"] or 0.0),
            str(item["appointment_date"]),
            str(item["slot_time"] or ""),
        ),
    )

    def _clinical_panels(item) -> dict[str, object]:
        questionnaire = _safe_json_loads(item["clinical_questionnaire_json"] if "clinical_questionnaire_json" in item.keys() else "")
        vitals = fetch_latest_patient_vitals(appointment_id=int(item["id"]), phone=str(item["phone"] or ""), patient_email=str(item["patient_email"] or ""))
        escalations = fetch_emergency_escalations(appointment_id=int(item["id"]))
        reports = []
        for report in fetch_report_analyses(int(item["id"])):
            try:
                lab_values = json.loads(report["lab_values_json"] or "{}")
            except json.JSONDecodeError:
                lab_values = {}
            try:
                abnormal_findings = json.loads(report["abnormal_findings_json"] or "[]")
            except json.JSONDecodeError:
                abnormal_findings = []
            reports.append(
                {
                    **dict(report),
                    "lab_values": lab_values if isinstance(lab_values, dict) else {},
                    "abnormal_findings": abnormal_findings if isinstance(abnormal_findings, list) else [],
                }
            )
        care_plans = fetch_care_plans(int(item["id"]))
        monitoring = fetch_monitoring_checkins(int(item["id"]))
        risk_breakdown = []
        if item["priority_score"] is not None:
            risk_breakdown.append({"label": "DOCQ priority score", "points": float(item["priority_score"]), "value": item["urgency"]})
        if questionnaire.get("answers"):
            risk_breakdown.append({"label": "Questionnaire responses captured", "points": len(questionnaire.get("answers", {})) * 2, "value": questionnaire.get("label", "Clinical questionnaire")})
        if vitals is not None and vitals["abnormal_flags_json"]:
            risk_breakdown.extend(_safe_json_loads(json.dumps({"items": json.loads(vitals["abnormal_flags_json"])})).get("items", []))
        return {
            "questionnaire": questionnaire,
            "latest_vitals": vitals,
            "emergency_escalations": escalations,
            "report_analyses": reports,
            "care_plans": care_plans,
            "monitoring_checkins": monitoring,
            "risk_breakdown": risk_breakdown,
        }

    enriched_pending = []
    for item in pending[:10]:
        enriched_pending.append(
            {
                "appointment": item,
                "history": get_patient_history(item["patient_name"], item["phone"], item["id"]),
                "clinical_diary": fetch_latest_clinical_diary(int(item["id"])),
                "prescription": fetch_latest_prescription(int(item["id"])),
                **_clinical_panels(item),
            }
        )
    enriched_appointments = []
    for item in appointments[:12]:
        enriched_appointments.append(
            {
                "appointment": item,
                "history": get_patient_history(item["patient_name"], item["phone"], item["id"]),
                "clinical_diary": fetch_latest_clinical_diary(int(item["id"])),
                "prescription": fetch_latest_prescription(int(item["id"])),
                **_clinical_panels(item),
            }
        )
    return {
        "doctor_name": doctor_name,
        "appointments": appointments,
        "recent_cases": enriched_appointments,
        "pending_count": len(pending),
        "pending_appointments": enriched_pending,
        "notifications": notifications,
        "slots": slots,
        "slot_breakdown": build_breakdown(slots, "status"),
    }
