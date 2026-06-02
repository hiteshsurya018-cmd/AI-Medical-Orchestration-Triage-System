from __future__ import annotations

import datetime as dt

from .appointments import create_appointment, update_appointment_status
from .calendar_integrations import sync_appointment_to_calendar
from .db import get_connection
from .human_coordination import enqueue_coordination_item
from .notifications import create_notification
from .operational_playbooks import handle_no_show_recovery
from .workflow_engine import CaseWorkflowEngine


def _appointment_exists(patient_email: str, appointment_date: str) -> bool:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT id FROM appointments WHERE patient_email = ? AND appointment_date = ?",
            (patient_email, appointment_date),
        ).fetchone()
    return row is not None


def bootstrap_demo_environment(config: dict[str, object]) -> dict[str, object]:
    today = dt.date.today()
    scenarios = [
        {
            "patient_name": "Mina Cardio",
            "patient_email": "demo.cardio@docq.local",
            "phone": "9000000001",
            "patient_age": 67,
            "medical_history": "hypertension, diabetes",
            "symptoms": "Persistent chest pain radiating to the shoulder",
            "specialty": "Cardiology",
            "appointment_date": (today + dt.timedelta(days=1)).isoformat(),
        },
        {
            "patient_name": "Dev General",
            "patient_email": "demo.general@docq.local",
            "phone": "9000000002",
            "patient_age": 34,
            "medical_history": "",
            "symptoms": "Fever, cough, and fatigue for two days",
            "specialty": "General",
            "appointment_date": (today + dt.timedelta(days=2)).isoformat(),
        },
        {
            "patient_name": "Sara Followup",
            "patient_email": "demo.followup@docq.local",
            "phone": "9000000003",
            "patient_age": 58,
            "medical_history": "asthma",
            "symptoms": "Routine follow-up visit after prior consultation",
            "specialty": "General",
            "appointment_date": (today + dt.timedelta(days=3)).isoformat(),
        },
    ]
    created_appointments = 0
    replay_workflows = 0

    for scenario in scenarios:
        if _appointment_exists(str(scenario["patient_email"]), str(scenario["appointment_date"])):
            continue
        appointment = create_appointment(scenario, actor_name="Demo Bootstrap", actor_role="admin", config=config)
        created_appointments += 1
        sync_appointment_to_calendar(int(appointment["id"]), provider="google", external_ref=f"demo-{appointment['id']}")
        create_notification(int(appointment["id"]), "patient", appointment["patient_name"], "email", "Demo reminder seeded", status="queued")
        if scenario["patient_email"] == "demo.followup@docq.local":
            update_appointment_status(int(appointment["id"]), status="no-show", queue_state="no-show")
            handle_no_show_recovery(int(appointment["id"]), worker_id="demo-bootstrap")
        if scenario["patient_email"] == "demo.general@docq.local":
            enqueue_coordination_item(
                queue_type="doctor_review",
                appointment_id=int(appointment["id"]),
                workflow_id=f"appointment-lifecycle:{appointment['id']}",
                priority=55,
                queue_status="pending",
                payload={"source": "demo-bootstrap", "label": "general review"},
            )

    engine = CaseWorkflowEngine()
    for workflow_id, message, age, history in [
        ("demo-replay-escalation", "Chest pain with dizziness and sweating", 72, "diabetes"),
        ("demo-replay-followup", "I have a headache and feel dizzy", 0, ""),
        ("demo-replay-scheduling", "Rash spreading on both arms", 26, ""),
    ]:
        with get_connection() as connection:
            exists = connection.execute("SELECT 1 FROM workflow_events WHERE workflow_id = ? LIMIT 1", (workflow_id,)).fetchone()
        if exists:
            continue
        replay_workflows += 1
        engine.run_intake(
            conversation_id=workflow_id,
            raw_message=message,
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=age if age else None,
            stored_history=history,
        )

    with get_connection() as connection:
        appointment_count = connection.execute("SELECT COUNT(*) FROM appointments WHERE patient_email LIKE 'demo.%@docq.local'").fetchone()[0]
        workflow_count = connection.execute("SELECT COUNT(DISTINCT workflow_id) FROM workflow_events WHERE workflow_id LIKE 'demo-%'").fetchone()[0]

    return {
        "status": "ok",
        "created_appointments": created_appointments,
        "appointment_count": int(appointment_count or 0),
        "created_replay_workflows": replay_workflows,
        "workflow_count": int(workflow_count or 0),
        "seeded_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
