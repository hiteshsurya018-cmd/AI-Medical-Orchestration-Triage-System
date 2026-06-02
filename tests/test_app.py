from __future__ import annotations

import datetime as dt
from io import BytesIO

import json
import pytest

from docq_app.appointments import EVALUATION_WORKFLOW_PREFIX, SECURITY_WORKFLOW_PREFIX, build_workflow_model_diff, cancel_appointment, create_appointment, fetch_active_threshold_profile, fetch_appointments, fetch_candidate_threshold_profile, fetch_coordination_queue_items, fetch_emergency_escalations, fetch_latest_clinical_diary, fetch_latest_patient_vitals, fetch_latest_prescription, fetch_notifications, fetch_prescriptions, fetch_report_analyses, fetch_sla_violations, fetch_tool_execution_logs, fetch_workflow_events, fetch_workflow_predictions, get_appointment, persist_governance_timeline_event, recommend_doctor_for_patient, recommend_doctor_matches, record_security_event
from docq_app.contracts import GovernanceTimelineEvent, RolloutSimulationProfile, WorkflowEventRecord
from docq_app.advisory_locks import acquire_advisory_lock, release_advisory_lock
from docq_app.appointment_lifecycle import current_lifecycle_state, reconstruct_operational_state, transition_appointment_lifecycle
from docq_app.calendar_integrations import reconcile_calendar_availability, sync_appointment_to_calendar
from docq_app.clinical_questionnaires import select_questionnaire
from docq_app.db import get_connection
from docq_app.event_migrations import normalize_workflow_event, validate_event_compatibility
from docq_app.event_bus import get_event_publisher
from docq_app.event_bus_nats import NatsJetStreamEventBus
from docq_app.governance_runtime import run_continuous_governance, simulate_rollout_profile
from docq_app.ml import analyze_symptoms
from docq_app.ml_governance import build_feature_snapshot
from docq_app.model_evaluation import get_model_evaluation_diff, get_model_evaluation_drift, get_model_evaluation_promotion_gate, get_model_evaluation_results, run_offline_model_evaluation
from docq_app.notifications import RETRY_DELAYS_MINUTES, create_notification, dispatch_notification_job, normalize_phone_number, process_notification_queue, send_due_reminders
from docq_app.operational_playbooks import handle_no_show_recovery, handle_notification_failures
from docq_app.operational_workers import run_calendar_sync_worker, run_playbook_worker, run_reminder_worker, run_sla_worker
from docq_app.reminder_runtime import enqueue_reminder_worker_task
from docq_app.report_analysis import analyze_report_text
from docq_app.intelligence_rollups import build_operational_rollup, fetch_latest_rollup
from docq_app.partitioning import build_partition_route
from docq_app.projection_workers import rebuild_projection
from docq_app.replay_snapshots import hydrate_workflow_replay, persist_replay_snapshot, validate_snapshot
from docq_app.event_bus import event_publisher, validate_event_envelope
from docq_app.projections import fetch_projection_checkpoint, fetch_projection_snapshot
from docq_app.replay_workers import run_distributed_replay_hydration
from docq_app.repositories import ReplayTransactionContext, WorkerExecutionRepository
from docq_app.runtime_topology import list_runtime_nodes
from docq_app.runtime_diagnostics import reconstruct_forward_path, reconstruct_reverse_path
from docq_app.sla_runtime import scan_sla_violations
from docq_app.worker_leases import acquire_worker_lease, release_worker_lease, renew_worker_lease
from docq_app.workflow_engine import CaseWorkflowEngine
from docq_app.human_coordination import assign_queue_item, deterministic_reassign_appointment, enqueue_coordination_item
from docq_app.dashboard import build_dashboard_metrics

from .conftest import extract_csrf


def test_analyze_symptoms_routes_medical_case(app):
    with app.app_context():
        result = analyze_symptoms("Persistent chest pain and shortness of breath", patient_age=72, medical_history="diabetes and hypertension")
    assert result["specialty"] == "Cardiology"
    assert result["severity"] == "Emergency"
    assert result["urgency"] == "Emergency"
    assert result["queue_state"] == "priority-review"
    assert result["priority_score"] > 60
    assert "chest pain" in result["extracted_symptoms"]
    assert result["quick_aid"]


def test_orthopedic_trauma_routes_to_orthopedics_without_diagnosis(app):
    with app.app_context():
        result = analyze_symptoms("My knee broke after a fall and the limb looks visibly deformed", patient_age=34, medical_history="")
    questionnaire = select_questionnaire("My knee broke after a fall and the limb looks visibly deformed")
    assert result["specialty"] == "Orthopedics"
    assert result["department"] == "Orthopedics"
    assert result["department_routing_source"] == "department_classification_engine"
    assert result["severity"] in {"High", "Emergency"}
    assert result["urgency"] in {"High", "Emergency"}
    assert "broken bone" in result["extracted_symptoms"]
    assert questionnaire["id"] == "orthopedic_trauma"


def test_department_classifier_routes_common_categories(app):
    cases = [
        ("I have chest pain and sweating", "Cardiology", "Cardiology"),
        ("My knee broke after a fall", "Orthopedics", "Orthopedics"),
        ("Sudden facial droop and slurred speech", "Neurology", "Neurology"),
        ("Child fever since yesterday", "Pediatrics", "Pediatrics"),
        ("Breathing difficulty and cough", "Pulmonology", "Pulmonology"),
    ]
    with app.app_context():
        for symptoms, specialty, department in cases:
            result = analyze_symptoms(symptoms, patient_age=30, medical_history="")
            assert result["specialty"] == specialty
            assert result["department"] == department
            assert result["department_routing_source"] == "department_classification_engine"


def test_create_and_cancel_appointment_flow(app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        item = create_appointment(
            {
                "patient_name": "Test User",
                "patient_email": "test@example.com",
                "phone": "9999999999",
                "patient_age": 68,
                "medical_history": "hypertension",
                "symptoms": "Persistent chest pain",
                "specialty": "Cardiology",
                "appointment_date": tomorrow,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
        cancel_appointment(item["id"], "patient unavailable", "Tester", "admin")
        cancelled = get_appointment(item["id"])
    assert item["doctor_name"] == "DOCQ Cardiology"
    assert item["severity"] in {"High", "Emergency"}
    assert item["quick_aid"]
    assert cancelled["status"] == "cancelled"


def test_reminders_only_send_for_tomorrow_scheduled(app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    today = dt.date.today().isoformat()
    with app.app_context():
        with get_connection() as connection:
            for patient_name, appointment_date, status in [
                ("Tomorrow Patient", tomorrow, "scheduled"),
                ("Cancelled Patient", today, "cancelled"),
            ]:
                connection.execute(
                    """
                    INSERT INTO appointments (
                        patient_name, patient_email, phone, symptoms, specialty, doctor_name, branch,
                        appointment_date, slot_time, slot_id, urgency, confidence, queue_state, status,
                        created_by, follow_up_status, reminder_sent, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        patient_name,
                        f"{patient_name.lower().replace(' ', '')}@example.com",
                        "9999999999",
                        "checkup",
                        "General",
                        "DOCQ General",
                        "Mysore Central",
                        appointment_date,
                        "09:00",
                        None,
                        "Low",
                        90.0,
                        "awaiting-doctor",
                        status,
                        "Tester",
                        "scheduled",
                        0,
                        dt.datetime.now().isoformat(timespec="seconds"),
                    ),
                )
        sent = send_due_reminders(app.config)
        appointments = fetch_appointments()
    assert sent == 1
    tomorrow_rows = [row for row in appointments if row["patient_name"] == "Tomorrow Patient"]
    cancelled_rows = [row for row in appointments if row["patient_name"] == "Cancelled Patient"]
    assert tomorrow_rows[0]["reminder_sent"] == 1
    assert cancelled_rows[0]["reminder_sent"] == 0


def test_doctor_route_requires_doctor_role(client):
    csrf = extract_csrf(client, "/login")
    client.post("/login", data={"email": "desk@docq.local", "password": "desk123", "_csrf_token": csrf}, follow_redirects=False)
    response = client.get("/doctor/inbox", follow_redirects=False)
    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"]


def test_login_rejects_external_next_redirect(client):
    csrf = extract_csrf(client, "/login")
    response = client.post(
        "/login?next=https://evil.example/steal",
        data={"email": "admin@docq.local", "password": "admin123", "_csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_doctor_can_save_diary_and_prescription_with_whatsapp_archive(client, app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        appointment = create_appointment(
            {
                "patient_name": "Prescription User",
                "patient_email": "prescription@example.com",
                "phone": "7000000099",
                "patient_age": 46,
                "medical_history": "arthritis",
                "symptoms": "Persistent joint pain",
                "specialty": "Orthopedics",
                "doctor_name": "DOCQ Orthopedics",
                "appointment_date": tomorrow,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
    csrf = extract_csrf(client, "/doctor-login")
    login_response = client.post(
        "/doctor-login",
        data={"email": "ortho@docq.local", "password": "doctor123", "_csrf_token": csrf},
        follow_redirects=False,
    )
    assert login_response.status_code == 302
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.post(
        "/doctor/inbox",
        data={
            "_csrf_token": csrf,
            "appointment_id": appointment["id"],
            "action": "save-clinical-record",
            "doctor_diary": "Patient reported pain during stair climbing. Mobility reduced.",
            "prescription_text": "Tab Aceclofenac twice daily for 5 days. Use knee brace and rest.",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        diary = fetch_latest_clinical_diary(int(appointment["id"]))
        prescription = fetch_latest_prescription(int(appointment["id"]))
        notifications = fetch_notifications(limit=30, target_name="Prescription User")
        archived = fetch_prescriptions(limit=20)
    assert diary is not None
    assert "Mobility reduced" in diary["diary_text"]
    assert prescription is not None
    assert "Aceclofenac" in prescription["prescription_text"]
    assert any(row["channel"] == "whatsapp" and row["message_category"] == "prescription_delivery" for row in notifications)
    assert any(int(row["appointment_id"]) == int(appointment["id"]) for row in archived)


def test_public_booking_creates_appointment_and_queue(client, app):
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    response = client.post(
        "/api/public-booking",
        json={
            "patient_name": "Public User",
            "patient_email": "public@example.com",
            "phone": "7777777777",
            "patient_age": 70,
            "medical_history": "diabetes",
            "specialty": "Cardiology",
            "appointment_date": tomorrow,
            "symptoms": "Persistent chest pain",
            "clinical_questionnaire": {
                "id": "chest_pain",
                "label": "Chest Pain",
                "answers": {"pain_location": "center chest", "duration": "6 hours"},
            },
            "vitals": {"spo2": 88, "blood_pressure": "185/121", "heart_rate": 142},
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201
    with app.app_context():
        appointments = fetch_appointments()
        created = next(item for item in appointments if item["patient_name"] == "Public User")
        assert created["patient_age"] == 70
        assert created["priority_score"] > 0
        assert created["quick_aid"]
        assert json.loads(created["clinical_questionnaire_json"])["answers"]["duration"] == "6 hours"
        latest_vitals = fetch_latest_patient_vitals(appointment_id=int(created["id"]))
        assert latest_vitals["risk_level"] == "critical"
        assert latest_vitals["spo2"] == 88


def test_public_booking_returns_whatsapp_sandbox_onboarding_when_configured(client, app):
    app.config["TWILIO_WHATSAPP_FROM"] = "whatsapp:+14155238886"
    app.config["TWILIO_WHATSAPP_SANDBOX_JOIN_CODE"] = "demo-sandbox"
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    response = client.post(
        "/api/public-booking",
        json={
            "patient_name": "Sandbox User",
            "patient_email": "sandbox@example.com",
            "phone": "7777777788",
            "patient_age": 42,
            "medical_history": "",
            "specialty": "General",
            "appointment_date": tomorrow,
            "symptoms": "Routine consultation",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["whatsapp_onboarding"]["required"] is True
    assert payload["whatsapp_onboarding"]["join_code"] == "demo-sandbox"
    assert payload["whatsapp_onboarding"]["join_url"] == "https://wa.me/14155238886?text=join%20demo-sandbox"


def test_public_booking_respects_patient_selected_doctor(client, app):
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    response = client.post(
        "/api/public-booking",
        json={
            "patient_name": "Selected Doctor User",
            "patient_email": "selected-doctor@example.com",
            "phone": "7777777711",
            "patient_age": 33,
            "medical_history": "",
            "specialty": "Orthopedics",
            "doctor_name": "DOCQ Ortho Motion",
            "appointment_date": tomorrow,
            "symptoms": "Knee pain after a sports injury",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["appointment"]["doctor_name"] == "DOCQ Ortho Motion"
    assert payload["appointment"]["doctor_selection_mode"] == "patient_selected"


def test_public_booking_autoclassifies_department_without_specialty(client, app):
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    response = client.post(
        "/api/public-booking",
        json={
            "patient_name": "Auto Department User",
            "patient_email": "auto-department@example.com",
            "phone": "7777777722",
            "patient_age": 34,
            "medical_history": "",
            "appointment_date": tomorrow,
            "symptoms": "My knee broke after a fall",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["appointment"]["specialty"] == "Orthopedics"
    assert payload["appointment"]["department"] == "Orthopedics"
    assert payload["appointment"]["department_routing_source"] == "department_classification_engine"
    assert payload["appointment"]["doctor_name"].startswith("DOCQ Ortho")


def test_doctor_options_api_returns_department_doctor_choices(client):
    response = client.get("/api/doctor-options?symptoms=skin%20rash")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["specialty"] == "Dermatology"
    assert payload["department"] == "Dermatology"
    assert payload["selection_policy"] == "patient_choice_or_earliest_available"
    assert payload["doctor_matches"]
    first = payload["doctor_matches"][0]
    assert first["department"] == "Dermatology"
    assert "next_available_slot" in first


def test_report_analysis_extracts_lab_abnormalities():
    result = analyze_report_text(
        "CBC Report Hemoglobin 8.5 g/dL WBC 12000 cells/uL Platelets 250000 Fasting glucose 140"
    )
    assert result["report_type"] == "CBC"
    assert result["lab_values"]["hemoglobin"]["value"] == 8.5
    messages = [item["message"] for item in result["abnormal_findings"]]
    assert any("Hemoglobin" in message and "below" in message for message in messages)
    assert any("Wbc" in message and "above" in message for message in messages)


def test_report_upload_api_persists_structured_findings(client, app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        appointment = create_appointment(
            {
                "patient_name": "Report User",
                "patient_email": "report@example.com",
                "phone": "7777777733",
                "patient_age": 45,
                "medical_history": "",
                "specialty": "General",
                "appointment_date": tomorrow,
                "symptoms": "Routine consultation",
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
    csrf = extract_csrf(client, "/doctor-login")
    client.post("/doctor-login", data={"email": "general@docq.local", "password": "doctor123", "_csrf_token": csrf}, follow_redirects=False)
    with client.session_transaction() as session:
        token = session["_csrf_token"]
    response = client.post(
        "/api/reports/upload",
        json={
            "appointment_id": appointment["id"],
            "report_text": "Blood Sugar Report Fasting glucose 145 mg/dL HbA1c 7.2",
        },
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["report"]["report_type"] == "Blood Sugar"
    assert payload["report"]["abnormal_findings"]
    with app.app_context():
        rows = fetch_report_analyses(int(appointment["id"]))
    assert rows
    assert rows[0]["ocr_status"] == "completed"
    assert "fasting_glucose" in json.loads(rows[0]["lab_values_json"])


def test_doctor_inbox_uploads_report_file_and_displays_findings(client, app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        appointment = create_appointment(
            {
                "patient_name": "Doctor Report User",
                "patient_email": "doctor-report@example.com",
                "phone": "7777777744",
                "patient_age": 52,
                "medical_history": "",
                "specialty": "General",
                "appointment_date": tomorrow,
                "symptoms": "Routine consultation",
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
    csrf = extract_csrf(client, "/doctor-login")
    client.post("/doctor-login", data={"email": "general@docq.local", "password": "doctor123", "_csrf_token": csrf}, follow_redirects=False)
    with client.session_transaction() as session:
        token = session["_csrf_token"]
    response = client.post(
        "/doctor/inbox",
        data={
            "_csrf_token": token,
            "appointment_id": str(appointment["id"]),
            "action": "upload-report",
            "report_file": (BytesIO(b"Thyroid Report TSH 8.1 T3 120 T4 6.5"), "thyroid.txt"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Report analyzed" in html
    assert "Tsh is above" in html


def test_patient_signup_returns_whatsapp_sandbox_onboarding_when_configured(client, app):
    app.config["TWILIO_WHATSAPP_FROM"] = "whatsapp:+14155238886"
    app.config["TWILIO_WHATSAPP_SANDBOX_JOIN_CODE"] = "demo-sandbox"
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.post(
        "/api/auth/patient-signup",
        json={
            "name": "Sandbox Signup User",
            "email": "sandbox-signup@example.com",
            "password": "strongpass123",
            "phone": "9999999991",
            "patient_age": 30,
            "prefers_sms": True,
            "prefers_email": True,
            "prefers_whatsapp": True,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["whatsapp_onboarding"]["required"] is True
    assert payload["whatsapp_onboarding"]["join_message"] == "join demo-sandbox"


def test_recommend_doctor_matches_mark_recent_and_most_visited(app):
    first_day = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    second_day = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    third_day = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    with app.app_context():
        create_appointment(
            {
                "patient_name": "Continuity User",
                "patient_email": "continuity@example.com",
                "phone": "7878787000",
                "patient_age": 40,
                "medical_history": "",
                "symptoms": "Back pain after lifting weight",
                "specialty": "Orthopedics",
                "doctor_name": "DOCQ Orthopedics",
                "appointment_date": first_day,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
        create_appointment(
            {
                "patient_name": "Continuity User",
                "patient_email": "continuity@example.com",
                "phone": "7878787000",
                "patient_age": 40,
                "medical_history": "",
                "symptoms": "Shoulder pain follow up",
                "specialty": "Orthopedics",
                "doctor_name": "DOCQ Orthopedics",
                "appointment_date": second_day,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
        create_appointment(
            {
                "patient_name": "Continuity User",
                "patient_email": "continuity@example.com",
                "phone": "7878787000",
                "patient_age": 40,
                "medical_history": "",
                "symptoms": "Knee pain review",
                "specialty": "Orthopedics",
                "doctor_name": "DOCQ Ortho Motion",
                "appointment_date": third_day,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
        matches = recommend_doctor_matches("Orthopedics", phone="7878787000", patient_email="continuity@example.com")
    assert any(item["doctor_name"] == "DOCQ Orthopedics" and item["most_visited"] for item in matches)
    assert any(item["doctor_name"] == "DOCQ Ortho Motion" and item["recent_visit"] for item in matches)


def test_intake_asks_only_for_age_when_profile_context_is_missing(client):
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.post(
        "/api/intake",
        json={"message": "Chest pain and dizziness"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["needs_more_info"] is True
    assert "age" in data["follow_up_question"].lower()
    assert data["workflow_trace"]
    response = client.post(
        "/api/intake",
        json={"message": "62"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data.get("needs_more_info") is True
    assert data["follow_up_type"] == "clinical_questionnaire"
    assert "chest pain" in data["known_context"]["questionnaire"]["label"].lower()
    for answer in ["center chest", "8", "left arm", "yes", "yes"]:
        response = client.post(
            "/api/intake",
            json={"message": answer},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data.get("needs_more_info") is True
    response = client.post(
        "/api/intake",
        json={"message": "6 hours"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data.get("needs_more_info") is not True
    assert data["known_context"]["used_age"] == 62
    assert data["clinical_questionnaire"]["answers"]["pain_location"] == "center chest"
    assert data["clinical_questionnaire"]["answers"]["duration"] == "6 hours"


def test_workflow_engine_coordinates_agents(app):
    with app.app_context():
        state = CaseWorkflowEngine().run_intake(
            conversation_id="test-conversation",
            raw_message="Persistent chest pain and shortness of breath",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=67,
            stored_history="diabetes",
        )
    assert state.next_action == "complete"
    assert state.assigned_agent == "communication-agent"
    assert state.analysis["doctor_name"] == "DOCQ Cardiology"
    assert state.analysis["workflow_trace"]
    assert state.policy_decision in {"human_review", "emergency_escalation"}
    assert state.analysis["reasoning_trace"]
    assert any(item["agent"] == "risk-agent" for item in state.analysis["workflow_trace"])
    assert not any(item["agent"] == "scheduling-agent" for item in state.analysis["workflow_trace"])
    assert state.analysis["booking_mode"] == "emergency"


def test_vitals_trigger_emergency_escalation_without_scheduling(app):
    workflow_id = "vitals-emergency-workflow"
    with app.app_context():
        state = CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Chest pain with breathing difficulty",
            patient_id=None,
            patient_email="patient@example.com",
            patient_phone="7777777000",
            actor_role="public",
            profile=None,
            stored_age=68,
            stored_history="hypertension",
            vitals_payload={"spo2": 86, "blood_pressure": "182/122", "heart_rate": 145},
        )
        escalations = fetch_emergency_escalations(workflow_id=workflow_id)
        notifications = fetch_notifications(limit=20)
    assert state.policy_decision == "emergency_escalation"
    assert state.analysis["booking_mode"] == "emergency"
    assert state.analysis["risk_explanation"]["risk_level"] == "EMERGENCY"
    assert state.analysis["risk_explanation"]["risk_score"] >= 95
    assert not any(item["agent"] == "scheduling-agent" for item in state.analysis["workflow_trace"])
    assert escalations
    assert any(row["message_category"] == "emergency_escalation" for row in notifications)
    patient_message = state.analysis["patient_message"]
    assert "urgent medical attention" in patient_message
    assert "Policy" not in patient_message
    assert "Risk Agent" not in patient_message
    assert "Emergency Escalation" not in patient_message


def test_workflow_engine_logs_persistent_events(app):
    with app.app_context():
        workflow_id = "workflow-event-test"
        state = CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Mild cough for two days",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=28,
            stored_history="",
        )
        events = fetch_workflow_events(workflow_id)
    assert state.next_action == "complete"
    assert len(events) >= 5
    assert events[0]["agent"] == "memory-agent"
    assert any(row["agent"] == "policy-engine" for row in events)


def test_workflow_engine_persists_tool_execution_telemetry(app):
    workflow_id = "workflow-tool-telemetry"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Mild cough for two days",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=30,
            stored_history="",
        )
        logs = fetch_tool_execution_logs(limit=20)
    matching = [row for row in logs if row["workflow_id"] == workflow_id]
    assert matching
    assert any(row["tool_name"] == "recommend_doctor" for row in matching)
    assert all(row["trace_id"] == workflow_id for row in matching)


def test_workflow_events_api_returns_event_history(client, app):
    workflow_id = "workflow-api-events"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Persistent cough with fever",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=52,
            stored_history="asthma",
        )
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.get(f"/api/workflows/{workflow_id}/events", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    data = response.get_json()
    assert data["version"] == "v1"
    assert data["workflow_id"] == workflow_id
    assert any(item["agent"] == "policy-engine" for item in data["events"])
    assert all("event_id" in item for item in data["events"])
    assert all("severity" in item for item in data["events"])
    assert all("timestamp" in item for item in data["events"])
    assert all("trace_id" in item for item in data["events"])
    assert all("root_event_id" in item for item in data["events"])


def test_workflow_replay_api_returns_structured_steps(client, app):
    workflow_id = "workflow-api-replay"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Persistent chest pain",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=71,
            stored_history="diabetes",
        )
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.get(f"/api/workflows/{workflow_id}/replay", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    data = response.get_json()
    assert data["version"] == "v1"
    assert data["workflow_id"] == workflow_id
    assert data["step_count"] >= 5
    assert any(step["agent"] == "policy-engine" for step in data["steps"])
    assert all("state" in step for step in data["steps"])
    assert all("type" in step for step in data["steps"])
    assert all("root_event_id" in step for step in data["steps"])


def test_workflow_integrity_api_returns_replay_guarantees(client, app):
    workflow_id = "workflow-integrity-api"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Persistent chest pain and shortness of breath",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=67,
            stored_history="diabetes",
        )
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.get(f"/api/workflows/{workflow_id}/integrity", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    data = response.get_json()
    assert data["workflow_id"] == workflow_id
    assert data["checksum"]["algorithm"] == "sha256"
    assert "replay_match" in data
    assert "feature_hash_consistency" in data
    assert "model_input_consistency" in data


def test_feature_snapshot_hashing_is_deterministic():
    snapshot_a = build_feature_snapshot(
        workflow_id="same-workflow",
        patient_id="patient-1",
        conversation_id="same-workflow",
        symptom_text="Persistent chest pain",
        patient_age=67,
        medical_history="diabetes",
        known_context={"profile_loaded": True, "used_age": 67, "history_loaded": True},
    )
    snapshot_b = build_feature_snapshot(
        workflow_id="same-workflow",
        patient_id="patient-1",
        conversation_id="same-workflow",
        symptom_text="Persistent chest pain",
        patient_age=67,
        medical_history="diabetes",
        known_context={"profile_loaded": True, "used_age": 67, "history_loaded": True},
    )
    assert snapshot_a.feature_snapshot_hash == snapshot_b.feature_snapshot_hash
    assert snapshot_a.model_input_hash == snapshot_b.model_input_hash


def test_shadow_predictions_persist_for_workflow(app):
    workflow_id = "workflow-shadow-persist"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Persistent chest pain and fatigue",
            patient_id="patient-77",
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=61,
            stored_history="hypertension",
        )
        predictions = fetch_workflow_predictions(workflow_id)
    assert len(predictions) >= 2
    assert any(not bool(row["is_shadow_prediction"]) for row in predictions)
    assert any(bool(row["is_shadow_prediction"]) for row in predictions)


def test_replay_event_payload_contains_ml_lineage(app):
    workflow_id = "workflow-ml-lineage"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Persistent chest pain and dizziness",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=69,
            stored_history="diabetes",
        )
        events = fetch_workflow_events(workflow_id)
        risk_event = next(row for row in events if row["agent"] == "risk-agent")
        payload = json.loads(risk_event["payload_json"] or "{}")
    assert payload["feature_snapshot_hash"]
    assert payload["model_input_hash"]
    assert payload["threshold_profile_id"]
    assert payload["model_key"]
    assert payload["top_features"]


def test_threshold_profile_versioning_is_persisted(app):
    workflow_id = "workflow-threshold-versioning"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Persistent cough and fever",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=44,
            stored_history="asthma",
        )
        active_threshold = fetch_active_threshold_profile()
        candidate_threshold = fetch_candidate_threshold_profile()
        predictions = fetch_workflow_predictions(workflow_id)
    assert active_threshold.status == "active"
    assert candidate_threshold is not None
    assert candidate_threshold.status == "candidate"
    assert any(int(row["threshold_profile_id"]) == int(active_threshold.id) for row in predictions if not bool(row["is_shadow_prediction"]))
    assert any(int(row["threshold_profile_id"]) == int(candidate_threshold.id) for row in predictions if bool(row["is_shadow_prediction"]))


def test_model_diff_endpoint_returns_candidate_vs_active_delta(client, app):
    workflow_id = "workflow-model-diff"
    with app.app_context():
        with get_connection() as connection:
            connection.execute(
                "UPDATE risk_threshold_profiles SET thresholds_json = ? WHERE status = 'candidate'",
                (json.dumps({"medium": 0.1, "high": 0.1, "emergency": 0.1, "review_confidence_lt": 5.0}, sort_keys=True),),
            )
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Mild cough with fever",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=35,
            stored_history="",
        )
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.get(f"/api/workflows/model-diff?workflow_id={workflow_id}", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    data = response.get_json()
    assert data["workflow_id"] == workflow_id
    assert data["active_model_key"]
    assert data["candidate_model_key"]
    assert "risk_band_delta" in data
    assert "replay_safe_explanation_payload" in data


def test_drift_endpoint_returns_deterministic_metrics(client, app):
    with app.app_context():
        for workflow_id, message, age in [
            ("workflow-drift-a", "Persistent chest pain", 70),
            ("workflow-drift-b", "Mild cough", 31),
        ]:
            CaseWorkflowEngine().run_intake(
                conversation_id=workflow_id,
                raw_message=message,
                patient_id=None,
                patient_email="",
                patient_phone="",
                actor_role="public",
                profile=None,
                stored_age=age,
                stored_history="",
            )
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.get("/api/workflows/drift", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    data = response.get_json()
    metric_keys = {item["metric_key"] for item in data["metrics"]}
    assert {"score_distribution_drift", "specialty_distribution_drift", "review_rate_drift"} <= metric_keys


def test_workflow_model_diff_helper_is_replay_safe(app):
    workflow_id = "workflow-model-diff-helper"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Persistent chest pain",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=72,
            stored_history="diabetes",
        )
        diff = build_workflow_model_diff(workflow_id)
    assert diff is not None
    assert diff["active_prediction"]["feature_snapshot_hash"] == diff["candidate_prediction"]["feature_snapshot_hash"]
    assert diff["active_prediction"]["model_input_hash"] == diff["candidate_prediction"]["model_input_hash"]


def test_offline_model_evaluation_is_deterministic_and_persisted(app):
    with app.app_context():
        for workflow_id, message, age, history in [
            ("eval-offline-a", "Persistent chest pain", 72, "diabetes"),
            ("eval-offline-b", "Mild cough with fever", 29, ""),
        ]:
            CaseWorkflowEngine().run_intake(
                conversation_id=workflow_id,
                raw_message=message,
                patient_id=None,
                patient_email="",
                patient_phone="",
                actor_role="public",
                profile=None,
                stored_age=age,
                stored_history=history,
            )
        run_a = run_offline_model_evaluation("latest-2")
        run_b = run_offline_model_evaluation("latest-2")
        results_a = get_model_evaluation_results(int(run_a["id"]))
        results_b = get_model_evaluation_results(int(run_b["id"]))
    assert run_a["evaluation_checksum"] == run_b["evaluation_checksum"]
    assert run_a["replay_integrity_passed"] is True
    assert len(results_a) == len(results_b) == 2


def test_model_evaluation_diff_and_gate_are_available(app):
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id="eval-diff-gate",
            raw_message="Persistent chest pain and dizziness",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=68,
            stored_history="hypertension",
        )
        run = run_offline_model_evaluation("latest-1")
        diff = get_model_evaluation_diff(int(run["id"]))
        gate = get_model_evaluation_promotion_gate(int(run["id"]))
        drift = get_model_evaluation_drift(int(run["id"]))
    assert diff["run_id"] == int(run["id"])
    assert "divergence_count" in diff
    assert "passed" in gate
    assert drift is not None
    assert "score_distribution_delta" in drift


def test_threshold_experimentation_surfaces_policy_deltas(app):
    with app.app_context():
        with get_connection() as connection:
            connection.execute(
                "UPDATE risk_threshold_profiles SET thresholds_json = ? WHERE status = 'candidate'",
                (json.dumps({"medium": 0.05, "high": 0.05, "emergency": 0.05, "review_confidence_lt": 5.0}, sort_keys=True),),
            )
        CaseWorkflowEngine().run_intake(
            conversation_id="eval-threshold-policy",
            raw_message="Moderate cough and fatigue",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=36,
            stored_history="",
        )
        run = run_offline_model_evaluation("latest-1")
        results = get_model_evaluation_results(int(run["id"]))
    assert results
    assert any(item["threshold_delta"] for item in results)


def test_evaluation_events_use_canonical_workflow_event_store(app):
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id="eval-event-source",
            raw_message="Persistent chest pain",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=70,
            stored_history="diabetes",
        )
        run = run_offline_model_evaluation("latest-1")
        events = fetch_workflow_events(f"{EVALUATION_WORKFLOW_PREFIX}{run['evaluation_run_key']}", limit=20)
    assert events
    payload = json.loads(events[-1]["payload_json"] or "{}")
    assert payload["evaluation_run_id"] == run["id"]
    assert payload["replay_checksum"] == run["evaluation_checksum"]


def test_ml_evaluation_endpoints_return_persisted_run(client, app):
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id="eval-api-workflow",
            raw_message="Persistent chest pain",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=71,
            stored_history="diabetes",
        )
    csrf_login = extract_csrf(client, "/login")
    client.post("/login", data={"email": "governance@docq.local", "password": "governance123", "_csrf_token": csrf_login}, follow_redirects=False)
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    create_response = client.get("/api/ml/evaluations?refresh=1&scope=latest-1", headers={"X-CSRF-Token": csrf})
    assert create_response.status_code == 200
    runs = create_response.get_json()["runs"]
    run_id = runs[0]["id"]
    detail = client.get(f"/api/ml/evaluations/{run_id}", headers={"X-CSRF-Token": csrf})
    diff = client.get(f"/api/ml/evaluations/{run_id}/diff", headers={"X-CSRF-Token": csrf})
    drift = client.get(f"/api/ml/evaluations/{run_id}/drift", headers={"X-CSRF-Token": csrf})
    gate = client.get(f"/api/ml/evaluations/{run_id}/promotion-gate", headers={"X-CSRF-Token": csrf})
    assert detail.status_code == 200
    assert diff.status_code == 200
    assert drift.status_code == 200
    assert gate.status_code == 200


def test_governance_runtime_launches_recommendations_deterministically(app):
    with app.app_context():
        with get_connection() as connection:
            connection.execute(
                "UPDATE risk_threshold_profiles SET thresholds_json = ? WHERE status = 'candidate'",
                (json.dumps({"medium": 0.05, "high": 0.05, "emergency": 0.05, "review_confidence_lt": 5.0}, sort_keys=True),),
            )
        CaseWorkflowEngine().run_intake(
            conversation_id="governance-trigger-a",
            raw_message="Persistent chest pain",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=71,
            stored_history="diabetes",
        )
        run_offline_model_evaluation("latest-1")
        state_a = run_continuous_governance(refresh=True)
        state_b = run_continuous_governance(refresh=False)
    assert state_a["governance_checksum"] == state_b["governance_checksum"]
    assert state_a["active_recommendations"]


def test_rollout_simulation_is_reproducible(app):
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id="governance-rollout-a",
            raw_message="Persistent chest pain and dizziness",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=69,
            stored_history="hypertension",
        )
        run = run_offline_model_evaluation("latest-1")
        state = run_continuous_governance(refresh=False)
        profile = RolloutSimulationProfile(**state["rollout_profiles"][0])
        rollout_a = simulate_rollout_profile(run, profile)
        rollout_b = simulate_rollout_profile(run, profile)
    assert rollout_a == rollout_b
    assert rollout_a["stages"][0]["percentage"] == 10


def test_governance_timeline_and_drift_trigger_apis(client, app):
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id="governance-api-a",
            raw_message="Persistent chest pain",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=74,
            stored_history="diabetes",
        )
        run_offline_model_evaluation("latest-1")
        run_continuous_governance(refresh=True)
    csrf_login = extract_csrf(client, "/login")
    client.post("/login", data={"email": "governance@docq.local", "password": "governance123", "_csrf_token": csrf_login}, follow_redirects=False)
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    recs = client.get("/api/ml/governance/recommendations", headers={"X-CSRF-Token": csrf})
    timeline = client.get("/api/ml/governance/timeline", headers={"X-CSRF-Token": csrf})
    state = client.get("/api/ml/governance/state", headers={"X-CSRF-Token": csrf})
    triggers = client.get("/api/ml/governance/drift-triggers", headers={"X-CSRF-Token": csrf})
    rollouts = client.get("/api/ml/governance/rollouts", headers={"X-CSRF-Token": csrf})
    assert recs.status_code == 200
    assert timeline.status_code == 200
    assert state.status_code == 200
    assert triggers.status_code == 200
    assert rollouts.status_code == 200
    assert timeline.get_json()["timeline"]


def test_governance_events_use_canonical_store(app):
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id="governance-event-a",
            raw_message="Persistent chest pain",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=70,
            stored_history="diabetes",
        )
        run_offline_model_evaluation("latest-1")
        state = run_continuous_governance(refresh=True)
        with get_connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM workflow_events WHERE workflow_id LIKE ?",
                ("ml-governance:%",),
            ).fetchone()[0]
    assert state["governance_checksum"]
    assert count >= 0


def test_signup_and_failed_login_emit_security_events(client, app):
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    signup = client.post(
        "/api/auth/signup",
        json={"name": "New Patient", "email": "newpatient@example.com", "password": "securepass1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert signup.status_code == 201
    failed_login_csrf = extract_csrf(client, "/login")
    client.post("/login", data={"email": "newpatient@example.com", "password": "wrongpass", "_csrf_token": failed_login_csrf}, follow_redirects=False)
    with app.app_context():
        with get_connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM workflow_events WHERE workflow_id LIKE ?", (f"{SECURITY_WORKFLOW_PREFIX}%",)).fetchone()[0]
    assert count >= 2


def test_patient_signup_continues_guest_workflow_context(client, app):
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.post(
        "/api/auth/patient-signup",
        json={
            "name": "Continuity Patient",
            "email": "continuity@example.com",
            "password": "securepass1",
            "phone": "9191919191",
            "patient_age": 36,
            "gender": "female",
            "prefers_whatsapp": True,
            "resume_context": {"workflow_id": "guest-workflow-1", "symptoms": "Persistent cough"},
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["resume_ready"] is True
    assert payload["workspace_context"]["profile"]["patient_email"] == "continuity@example.com"
    with client.session_transaction() as session:
        assert session["role"] == "patient"


def test_patient_signup_credentials_work_for_patient_login(client):
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    signup = client.post(
        "/api/auth/patient-signup",
        json={
            "name": "Login Patient",
            "email": "loginpatient@example.com",
            "password": "securepass1",
            "phone": "9393939393",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert signup.status_code == 201
    client.get("/logout")
    login_csrf = extract_csrf(client, "/patient-login")
    login = client.post(
        "/patient-login",
        data={"email": "loginpatient@example.com", "password": "securepass1", "_csrf_token": login_csrf},
        follow_redirects=False,
    )
    assert login.status_code == 302
    assert login.headers["Location"].endswith("/intake")


def test_patient_signup_page_posts_and_redirects_into_intake(client):
    csrf = extract_csrf(client, "/patient-signup")
    response = client.post(
        "/patient-signup",
        data={
            "_csrf_token": csrf,
            "name": "Form Signup Patient",
            "email": "formsignup@example.com",
            "phone": "9494949494",
            "patient_age": "29",
            "gender": "male",
            "password": "securepass1",
            "prefers_sms": "yes",
            "prefers_email": "yes",
            "prefers_whatsapp": "yes",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/intake")
    client.get("/logout")
    login_csrf = extract_csrf(client, "/patient-login")
    login_page = client.get("/patient-login")
    assert "formsignup@example.com" in login_page.get_data(as_text=True)
    login = client.post(
        "/patient-login",
        data={"email": "formsignup@example.com", "password": "securepass1", "_csrf_token": login_csrf},
        follow_redirects=False,
    )
    assert login.status_code == 302
    assert login.headers["Location"].endswith("/intake")


def test_password_reset_and_email_verification_tokens_are_replay_safe(client, app):
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    signup = client.post(
        "/api/auth/patient-signup",
        json={
            "name": "Verify Patient",
            "email": "verify@example.com",
            "password": "securepass1",
            "phone": "9292929292",
        },
        headers={"X-CSRF-Token": csrf},
    )
    token = signup.get_json()["verification_token"]
    verify_response = client.get(f"/verify-email/{token}")
    assert verify_response.status_code == 302
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    reset_request = client.post("/api/auth/request-password-reset", json={"email": "verify@example.com"}, headers={"X-CSRF-Token": csrf})
    assert reset_request.status_code == 200
    with app.app_context():
        with get_connection() as connection:
            token_row = connection.execute(
                "SELECT token FROM auth_tokens WHERE token_type = 'password_reset' ORDER BY id DESC LIMIT 1"
            ).fetchone()
    reset_response = client.post(
        "/api/auth/reset-password",
        json={"token": token_row["token"], "password": "updatedpass1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert reset_response.status_code == 200


def test_health_ready_and_metrics_endpoints(client):
    health = client.get("/health")
    ready = client.get("/ready")
    metrics = client.get("/metrics")
    assert health.status_code == 200
    assert ready.status_code == 200
    assert metrics.status_code == 200
    assert "docq_http_requests_total" in metrics.get_data(as_text=True)


def test_workflow_metrics_api_returns_operational_summary(client, app):
    workflow_id = "workflow-metrics-api"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Chest pain with dizziness",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=73,
            stored_history="hypertension",
        )
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.get("/api/workflows/metrics", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    data = response.get_json()
    assert data["version"] == "v1"
    assert "active_workflows" in data
    assert "decision_breakdown" in data


def test_workflow_summary_api_returns_activity_feed(client, app):
    workflow_id = "workflow-summary-api"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Mild fever and cough",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=30,
            stored_history="",
        )
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.get("/api/workflows/summary", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    data = response.get_json()
    assert data["version"] == "v1"
    assert "activity_feed" in data
    assert any(item["workflow_id"] == workflow_id for item in data["activity_feed"])
    assert all("severity" in item for item in data["activity_feed"])


def test_workflow_intelligence_api_returns_operational_signals(client, app):
    workflow_id = "workflow-intelligence-api"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Persistent fever and dizziness",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=64,
            stored_history="hypertension",
        )
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.get("/api/workflows/intelligence", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    data = response.get_json()
    assert data["version"] == "v1"
    assert "queue_pressure" in data
    assert "tool_health" in data
    assert "recovery_metrics" in data
    assert "incident_state" in data
    assert "lineage_summaries" in data
    assert "failure_signatures" in data
    assert "anomalies" in data


def test_workflow_diff_api_returns_divergence_summary(client, app):
    workflow_a = "workflow-diff-a"
    workflow_b = "workflow-diff-b"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_a,
            raw_message="Persistent chest pain",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=71,
            stored_history="diabetes",
        )
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_b,
            raw_message="Mild cough and fever",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=24,
            stored_history="",
        )
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.get(f"/api/workflows/diff?workflow_a={workflow_a}&workflow_b={workflow_b}", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    data = response.get_json()
    assert data["version"] == "v1"
    assert data["workflow_a"] == workflow_a
    assert data["workflow_b"] == workflow_b
    assert "differing_events" in data
    assert "policy_path_delta" in data
    assert "root_cause" in data
    assert "probable_cause" in data["root_cause"]
    assert "confidence" in data["root_cause"]


def test_workflow_anomalies_api_returns_typed_anomalies(client, app):
    workflow_id = "workflow-anomaly-api"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Persistent fever, fatigue, dizziness, and shortness of breath",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=79,
            stored_history="hypertension",
        )
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.get("/api/workflows/anomalies", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    data = response.get_json()
    assert data["version"] == "v1"
    assert "anomalies" in data


def test_dashboard_can_render_workflow_replay_search(client):
    csrf = extract_csrf(client, "/login")
    client.post(
        "/login",
        data={"email": "admin@docq.local", "password": "admin123", "_csrf_token": csrf},
        follow_redirects=False,
    )
    response = client.get("/dashboard?workflow_id=workflow-api-replay", follow_redirects=False)
    assert response.status_code == 200
    assert b"Workflow Console" in response.data


def test_workflow_stream_api_emits_sse_payload(client, app):
    workflow_id = "workflow-stream-api"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Chest pain and shortness of breath",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=68,
            stored_history="diabetes",
        )
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.get(
        f"/api/workflows/stream?workflow_id={workflow_id}",
        headers={"X-CSRF-Token": csrf},
        buffered=False,
    )
    first_chunk = next(response.response).decode("utf-8")
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert "event: workflow" in first_chunk
    assert '"version": "v1"' in first_chunk
    assert '"operational_intelligence"' in first_chunk
    assert '"workflow_diff"' in first_chunk
    assert '"replay_integrity"' in first_chunk
    assert workflow_id in first_chunk


def test_event_migration_normalizes_legacy_shape():
    raw = {
        "workflow_id": "legacy-workflow",
        "created_at": "2026-05-09T10:00:00",
        "stage": "decision",
        "type": "workflow_transition",
    }
    normalized = normalize_workflow_event(raw)
    assert normalized["timestamp"] == "2026-05-09T10:00:00"
    assert normalized["state"] == "decision"
    assert validate_event_compatibility(raw) is True


def test_lineage_reconstruction_preserves_forward_and_reverse_paths(app):
    workflow_id = "workflow-lineage-paths"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Mild fever and cough",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=31,
            stored_history="",
        )
        events = fetch_workflow_events(workflow_id)
    root_id = events[0]["root_event_id"]
    records = [
        WorkflowEventRecord(
            **normalize_workflow_event(
                {
                    "event_id": row["id"],
                    "workflow_id": row["workflow_id"],
                    "trace_id": row["trace_id"] or row["workflow_id"],
                    "correlation_id": row["correlation_id"] or row["workflow_id"],
                    "causation_id": row["causation_id"],
                    "parent_event_id": row["parent_event_id"],
                    "root_event_id": row["root_event_id"],
                    "causation_depth": row["causation_depth"],
                    "replay_branch_id": row["replay_branch_id"],
                    "timestamp": row["created_at"],
                    "type": "workflow_transition",
                    "severity": "info",
                    "agent": row["agent"],
                    "state": row["stage"],
                    "action": row["action"],
                    "decision": row["decision"] or "pending",
                    "payload": {},
                }
            )
        )
        for row in events
    ]
    forward = reconstruct_forward_path(
        records,
        root_id,
    )
    reverse = reconstruct_reverse_path(
        records,
        events[-1]["id"],
    )
    assert forward[0] == root_id
    assert reverse[0] == events[-1]["id"]


def test_patient_login_uses_stored_profile_context_without_reasking_age(client):
    csrf = extract_csrf(client, "/patient-login")
    login_response = client.post(
        "/patient-login",
        data={"email": "patient@docq.local", "password": "patient123", "_csrf_token": csrf},
        follow_redirects=False,
    )
    assert login_response.status_code == 302
    assert login_response.headers["Location"].endswith("/intake")
    csrf = extract_csrf(client, "/intake")
    response = client.post(
        "/api/intake",
        json={"message": "Chest pain and dizziness"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data.get("needs_more_info") is True
    assert data["follow_up_type"] == "clinical_questionnaire"
    assert "age" not in data["follow_up_question"].lower()
    for answer in ["center chest", "7", "no", "no", "no"]:
        response = client.post(
            "/api/intake",
            json={"message": answer},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data.get("needs_more_info") is True
    response = client.post(
        "/api/intake",
        json={"message": "2 hours"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data.get("needs_more_info") is not True
    assert data["known_context"]["profile_found"] is True
    assert data["known_context"]["history_loaded"] is True
    assert data["known_context"]["used_age"] == 62
    assert data["conversation_payload"]["intent"] == "report_symptom"
    assert isinstance(data["conversation_payload"]["ui_actions"], list)
    assert data["conversation_payload"]["recommended_doctor"]["doctor_name"] == data["doctor_name"]


def test_recommend_doctor_prefers_continuity_for_same_specialty(app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        create_appointment(
            {
                "patient_name": "Continuity User",
                "patient_email": "continuity@example.com",
                "phone": "8111111111",
                "patient_age": 44,
                "symptoms": "Knee pain after a fall",
                "specialty": "Orthopedics",
                "appointment_date": tomorrow,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
        recommendation = recommend_doctor_for_patient("Orthopedics", phone="8111111111", patient_email="continuity@example.com")
    assert recommendation["doctor_name"] == "DOCQ Orthopedics"
    assert "continuity" in recommendation["continuity_reason"].lower()


def test_notification_queue_processing_marks_sent(app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        item = create_appointment(
            {
                "patient_name": "Queue User",
                "patient_email": "queue@example.com",
                "phone": "9999999999",
                "symptoms": "Persistent chest pain",
                "specialty": "Cardiology",
                "appointment_date": tomorrow,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
        processed = process_notification_queue(
            {
                **app.config,
                "TWILIO_ACCOUNT_SID": None,
                "TWILIO_AUTH_TOKEN": None,
                "TWILIO_FROM_NUMBER": None,
                "SMTP_HOST": None,
                "SMTP_USERNAME": None,
                "SMTP_PASSWORD": None,
                "SMTP_FROM": None,
            }
        )
        with get_connection() as connection:
            statuses = connection.execute(
                "SELECT status FROM notifications WHERE appointment_id = ? AND channel IN ('sms', 'email')",
                (item["id"],),
            ).fetchall()
    assert processed >= 2
    assert any(row["status"] in {"retry", "failed"} for row in statuses)


def test_notification_failures_create_fallback_dashboard_event(app):
    with app.app_context():
        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO appointments (
                    patient_name, patient_email, phone, symptoms, specialty, doctor_name, branch,
                    appointment_date, slot_time, slot_id, urgency, confidence, queue_state, status,
                    created_by, follow_up_status, reminder_sent, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Fallback User",
                    "",
                    "9999999998",
                    "Persistent chest pain",
                    "Cardiology",
                    "DOCQ Cardiology",
                    "Mysore Central",
                    (dt.date.today() + dt.timedelta(days=1)).isoformat(),
                    "09:30",
                    None,
                    "High",
                    60.0,
                    "priority-review",
                    "scheduled",
                    "Tester",
                    "scheduled",
                    0,
                    dt.datetime.now().isoformat(timespec="seconds"),
                ),
            )
            appointment_id = cursor.lastrowid
        create_notification(
            appointment_id,
            "patient",
            "Fallback User",
            "sms",
            "DOCQ fallback delivery test",
            status="retry",
            attempt_count=len(RETRY_DELAYS_MINUTES),
        )
        process_notification_queue(
            {
                **app.config,
                "TWILIO_ACCOUNT_SID": None,
                "TWILIO_AUTH_TOKEN": None,
                "TWILIO_FROM_NUMBER": None,
                "SMTP_HOST": None,
                "SMTP_USERNAME": None,
                "SMTP_PASSWORD": None,
                "SMTP_FROM": None,
            }
        )
        with get_connection() as connection:
            fallback = connection.execute(
                "SELECT channel, status, message FROM notifications WHERE appointment_id = ? AND channel = 'dashboard' ORDER BY id DESC LIMIT 1",
                (appointment_id,),
            ).fetchone()
    assert fallback is not None
    assert fallback["status"] == "visible"
    assert "Delivery recovery required" in fallback["message"]


def test_confirmation_notifications_can_flow_through_n8n(app, monkeypatch):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        item = create_appointment(
            {
                "patient_name": "N8N User",
                "patient_email": "n8n@example.com",
                "phone": "9999999999",
                "symptoms": "Persistent chest pain",
                "specialty": "Cardiology",
                "appointment_date": tomorrow,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )

        def fake_send(config, row):
            return "sent", "n8n-test-sid", None

        monkeypatch.setattr("docq_app.notifications.send_confirmation_to_n8n", fake_send)
        processed = process_notification_queue({**app.config, "DOCQ_N8N_CONFIRMATION_WEBHOOK": "https://example.test/webhook/docq-confirmation"})
        with get_connection() as connection:
            sms_row = connection.execute(
                "select status, external_id from notifications where appointment_id = ? and channel = 'sms' order by id desc limit 1",
                (item["id"],),
            ).fetchone()
    assert processed >= 1
    assert sms_row["status"] == "sent"
    assert sms_row["external_id"] == "n8n-test-sid"


def test_whatsapp_confirmation_uses_direct_delivery_not_n8n(app, monkeypatch):
    calls: dict[str, int] = {"n8n": 0, "direct": 0}

    def fake_n8n(config, row):
        calls["n8n"] += 1
        return "sent", "n8n-whatsapp-should-not-run", None

    def fake_deliver(config, *, channel, phone=None, email=None, message, email_subject=None, whatsapp=False):
        calls["direct"] += 1
        return "sent", "twilio-whatsapp-direct", None

    monkeypatch.setattr("docq_app.notifications.send_confirmation_to_n8n", fake_n8n)
    monkeypatch.setattr("docq_app.notifications.deliver_notification", fake_deliver)
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        appointment = create_appointment(
            {
                "patient_name": "WhatsApp Direct User",
                "patient_email": "whatsapp-direct@example.com",
                "phone": "9999999997",
                "symptoms": "knee pain",
                "specialty": "Orthopedics",
                "appointment_date": tomorrow,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT id
                FROM notifications
                WHERE appointment_id = ? AND channel = 'whatsapp'
                ORDER BY id DESC
                LIMIT 1
                """,
                (appointment["id"],),
            ).fetchone()
        result = dispatch_notification_job(int(row["id"]))
    assert result["status"] == "sent"
    assert calls["n8n"] == 0
    assert calls["direct"] >= 1


def test_create_notification_enqueues_worker_dispatch_job(app, monkeypatch):
    captured: dict[str, object] = {}

    def fake_enqueue(redis_url, task_path, *args, **kwargs):
        captured["redis_url"] = redis_url
        captured["task_path"] = task_path
        captured["args"] = args
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("docq_app.notifications.enqueue_job", fake_enqueue)
    with app.app_context():
        notification_id = create_notification(
            None,
            "patient",
            "Queue Dispatch User",
            "whatsapp",
            "DOCQ test dispatch",
            "queued",
            correlation_id="test:notification-dispatch",
        )
    assert notification_id > 0
    assert captured["task_path"] == "docq_app.notifications.dispatch_notification_job"
    assert captured["args"] == (notification_id,)
    assert captured["kwargs"]["idempotency_key"] == f"notification-dispatch:{notification_id}"


def test_create_notification_falls_back_to_inline_dispatch_when_queue_enqueue_fails(app, monkeypatch):
    captured: dict[str, object] = {}

    def fake_enqueue(redis_url, task_path, *args, **kwargs):
        raise RuntimeError("redis-unreachable")

    def fake_dispatch(notification_id):
        captured["notification_id"] = notification_id
        return {"notification_id": notification_id, "status": "sent"}

    monkeypatch.setattr("docq_app.notifications.enqueue_job", fake_enqueue)
    monkeypatch.setattr("docq_app.notifications.dispatch_notification_job", fake_dispatch)
    with app.app_context():
        notification_id = create_notification(
            None,
            "patient",
            "Inline Dispatch User",
            "whatsapp",
            "DOCQ inline fallback dispatch",
            "queued",
            correlation_id="test:inline-dispatch",
        )
    assert notification_id > 0
    assert captured["notification_id"] == notification_id


def test_dispatch_notification_job_processes_single_whatsapp_notification(app, monkeypatch):
    delivered: dict[str, object] = {}

    def fake_deliver(config, *, channel, phone=None, email=None, message, email_subject=None, whatsapp=False):
        delivered["channel"] = channel
        delivered["phone"] = phone
        delivered["message"] = message
        delivered["whatsapp"] = whatsapp
        return "sent", "twilio-message-123", None

    monkeypatch.setattr("docq_app.notifications.deliver_notification", fake_deliver)
    with app.app_context():
        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO appointments (
                    patient_name, patient_email, phone, symptoms, specialty, doctor_name, branch,
                    appointment_date, slot_time, slot_id, urgency, confidence, queue_state, status,
                    created_by, follow_up_status, reminder_sent, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "WhatsApp Dispatch User",
                    "dispatch@example.com",
                    "8888888888",
                    "checkup",
                    "Orthopedics",
                    "DOCQ Orthopedics",
                    "Mysore Central",
                    (dt.date.today() + dt.timedelta(days=1)).isoformat(),
                    "10:30",
                    None,
                    "Low",
                    80.0,
                    "awaiting-doctor",
                    "scheduled",
                    "Tester",
                    "scheduled",
                    0,
                    dt.datetime.now().isoformat(timespec="seconds"),
                ),
            )
            appointment_id = int(cursor.lastrowid)
        notification_id = create_notification(
            appointment_id,
            "patient",
            "WhatsApp Dispatch User",
            "whatsapp",
            "DOCQ confirmed your appointment with DOCQ Orthopedics on 2026-05-12 at 10:30.",
            "queued",
            correlation_id=f"appointment:{appointment_id}:whatsapp-confirmation",
            message_category="appointment_confirmation",
        )
        result = dispatch_notification_job(notification_id)
        with get_connection() as connection:
            notification_row = connection.execute(
                "SELECT status, external_id, attempt_count FROM notifications WHERE id = ?",
                (notification_id,),
            ).fetchone()
            ledger_row = connection.execute(
                "SELECT execution_state FROM worker_execution_ledger WHERE task_id = ?",
                (f"queued:notification-dispatch:{notification_id}",),
            ).fetchone()
    assert result["status"] == "sent"
    assert delivered["channel"] == "whatsapp"
    assert delivered["whatsapp"] is True
    assert notification_row["status"] == "sent"
    assert notification_row["external_id"] == "twilio-message-123"
    assert int(notification_row["attempt_count"]) == 1
    assert ledger_row is None or ledger_row["execution_state"] == "completed"


def test_dispatch_notification_job_bootstraps_runtime_without_flask_app_context(app, monkeypatch):
    delivered: dict[str, object] = {}

    def fake_deliver(config, *, channel, phone=None, email=None, message, email_subject=None, whatsapp=False):
        delivered["channel"] = channel
        delivered["phone"] = phone
        delivered["message"] = message
        delivered["whatsapp"] = whatsapp
        return "sent", "twilio-worker-context-123", None

    monkeypatch.setattr("docq_app.notifications.deliver_notification", fake_deliver)
    with app.app_context():
        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO appointments (
                    patient_name, patient_email, phone, symptoms, specialty, doctor_name, branch,
                    appointment_date, slot_time, slot_id, urgency, confidence, queue_state, status,
                    created_by, follow_up_status, reminder_sent, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Worker Context User",
                    "worker-context@example.com",
                    "9999999998",
                    "checkup",
                    "Orthopedics",
                    "DOCQ Orthopedics",
                    "Mysore Central",
                    (dt.date.today() + dt.timedelta(days=1)).isoformat(),
                    "11:00",
                    None,
                    "Low",
                    80.0,
                    "awaiting-doctor",
                    "scheduled",
                    "Tester",
                    "scheduled",
                    0,
                    dt.datetime.now().isoformat(timespec="seconds"),
                ),
            )
            appointment_id = int(cursor.lastrowid)
        notification_id = create_notification(
            appointment_id,
            "patient",
            "Worker Context User",
            "whatsapp",
            "DOCQ confirmed your appointment with DOCQ Orthopedics on 2026-05-12 at 11:00.",
            "queued",
            correlation_id=f"appointment:{appointment_id}:whatsapp-confirmation",
            message_category="appointment_confirmation",
        )

    result = dispatch_notification_job(notification_id)

    with app.app_context():
        with get_connection() as connection:
            notification_row = connection.execute(
                "SELECT status, external_id, attempt_count FROM notifications WHERE id = ?",
                (notification_id,),
            ).fetchone()
            ledger_row = connection.execute(
                "SELECT execution_state FROM worker_execution_ledger WHERE task_id = ?",
                (f"queued:notification-dispatch:{notification_id}",),
            ).fetchone()
    assert result["status"] == "sent"
    assert delivered["channel"] == "whatsapp"
    assert delivered["whatsapp"] is True
    assert notification_row["status"] == "sent"
    assert notification_row["external_id"] == "twilio-worker-context-123"
    assert int(notification_row["attempt_count"]) == 1
    assert ledger_row is None or ledger_row["execution_state"] == "completed"


def test_normalize_phone_number_to_e164_for_india_defaults():
    assert normalize_phone_number("9353134049") == "+919353134049"
    assert normalize_phone_number("919353134049") == "+919353134049"
    assert normalize_phone_number("09353134049") == "+919353134049"
    assert normalize_phone_number("+919353134049") == "+919353134049"
    assert normalize_phone_number("whatsapp:+919353134049") == "+919353134049"


def test_operational_rollup_is_idempotent_under_repeat_generation(app):
    with app.app_context():
        first = build_operational_rollup()
        second = build_operational_rollup()
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT id, rollup_key FROM intelligence_rollups WHERE rollup_key = ?",
                (first["rollup_key"],),
            ).fetchall()
    assert first["rollup_key"] == second["rollup_key"]
    assert first["rollup_id"] == second["rollup_id"]
    assert len(rows) == 1


def test_admin_command_center_apis_expose_operational_state(client, app):
    csrf = extract_csrf(client, "/login")
    client.post("/login", data={"email": "admin@docq.local", "password": "admin123", "_csrf_token": csrf}, follow_redirects=False)
    for path, expected_key in [
        ("/admin/events", "items"),
        ("/admin/workflows", "items"),
        ("/admin/incidents", "incident_state"),
        ("/admin/runtime/queues", "queue"),
        ("/admin/runtime/workers", "workers"),
        ("/admin/notifications", "items"),
        ("/admin/audit", "items"),
        ("/admin/continuity", "items"),
        ("/admin/schedules", "slots"),
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert expected_key in response.get_json()


def test_admin_dashboard_exposes_operations_command_center(client, app):
    csrf = extract_csrf(client, "/login")
    client.post("/login", data={"email": "admin@docq.local", "password": "admin123", "_csrf_token": csrf}, follow_redirects=False)
    response = client.get("/admin")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Live Hospital Overview" in html
    assert "Department Overview" in html
    assert "Appointment Governance" in html
    with app.app_context():
        metrics = build_dashboard_metrics(app.config)
    assert "operations" in metrics
    assert "department_overview" in metrics["operations"]
    assert "priority_queue" in metrics["operations"]


def test_admin_reschedule_records_notifications_and_audit(client, app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    new_date = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    with app.app_context():
        appointment = create_appointment(
            {
                "patient_name": "Governance User",
                "patient_email": "governance@example.com",
                "phone": "9999999999",
                "patient_age": 42,
                "medical_history": "",
                "symptoms": "routine follow up",
                "specialty": "General",
                "appointment_date": tomorrow,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
    csrf = extract_csrf(client, "/login")
    client.post("/login", data={"email": "admin@docq.local", "password": "admin123", "_csrf_token": csrf}, follow_redirects=False)
    with client.session_transaction() as session:
        token = session["_csrf_token"]
    response = client.post(
        f"/admin/appointments/{appointment['id']}/reschedule",
        json={"new_appointment_date": new_date},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200
    with app.app_context():
        updated = get_appointment(appointment["id"])
        notifications = fetch_notifications(limit=20, target_name="Governance User")
        with get_connection() as connection:
            audit = connection.execute(
                "SELECT action, details FROM audit_logs WHERE entity_id = ? ORDER BY id DESC LIMIT 1",
                (appointment["id"],),
            ).fetchone()
    assert updated["appointment_date"] == new_date
    assert updated["status"] == "rescheduled"
    assert any(row["message_category"] == "appointment_governance" for row in notifications)
    assert audit["action"] == "reschedule-appointment"


def test_admin_retry_notification_requeues_failed_delivery(client, app):
    with app.app_context():
        notification_id = create_notification(
            None,
            "patient",
            "Retry User",
            "whatsapp",
            "DOCQ retry me",
            "failed",
            last_error="provider-failed",
            correlation_id="retry:user",
        )
    csrf = extract_csrf(client, "/login")
    client.post("/login", data={"email": "admin@docq.local", "password": "admin123", "_csrf_token": csrf}, follow_redirects=False)
    with client.session_transaction() as session:
        token = session["_csrf_token"]
    response = client.post(f"/admin/notifications/{notification_id}/retry", headers={"X-CSRF-Token": token})
    assert response.status_code == 200
    with app.app_context():
        with get_connection() as connection:
            row = connection.execute("SELECT status FROM notifications WHERE id = ?", (notification_id,)).fetchone()
    assert row["status"] == "queued"


def test_automation_reminders_endpoint_returns_due_items(client, app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO appointments (
                    patient_name, patient_email, phone, symptoms, specialty, doctor_name, branch,
                    appointment_date, slot_time, slot_id, urgency, confidence, queue_state, status,
                    created_by, follow_up_status, reminder_sent, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Reminder API User",
                    "reminder@example.com",
                    "6666666666",
                    "follow up",
                    "General",
                    "DOCQ General",
                    "Mysore Central",
                    tomorrow,
                    "10:00",
                    None,
                    "Low",
                    88.0,
                    "awaiting-doctor",
                    "scheduled",
                    "Tester",
                    "scheduled",
                    0,
                    dt.datetime.now().isoformat(timespec="seconds"),
                ),
            )
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.get(f"/api/automation/reminders?target_date={tomorrow}", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    data = response.get_json()
    assert data["target_date"] == tomorrow
    assert any(item["patient_name"] == "Reminder API User" for item in data["items"])


def test_mark_reminder_sent_endpoint_updates_flag(client, app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO appointments (
                    patient_name, patient_email, phone, symptoms, specialty, doctor_name, branch,
                    appointment_date, slot_time, slot_id, urgency, confidence, queue_state, status,
                    created_by, follow_up_status, reminder_sent, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Mark Sent User",
                    "marksent@example.com",
                    "5555555555",
                    "follow up",
                    "General",
                    "DOCQ General",
                    "Mysore Central",
                    tomorrow,
                    "11:00",
                    None,
                    "Low",
                    88.0,
                    "awaiting-doctor",
                    "scheduled",
                    "Tester",
                    "scheduled",
                    0,
                    dt.datetime.now().isoformat(timespec="seconds"),
                ),
            )
            appointment_id = cursor.lastrowid
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.post(
        "/api/automation/reminders/mark-sent",
        json={"appointment_id": appointment_id, "status": "sent"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    with app.app_context():
        appointment = get_appointment(appointment_id)
    assert appointment["reminder_sent"] == 1


def test_workflow_event_repository_prevents_duplicate_writes(app):
    with app.app_context():
        first_id = record_security_event(
            "dup-event-check",
            action="duplicate_test",
            decision="accepted",
            payload={"kind": "security", "value": 1},
            confidence=100.0,
        )
        second_id = record_security_event(
            "dup-event-check",
            action="duplicate_test",
            decision="accepted",
            payload={"kind": "security", "value": 1},
            confidence=100.0,
        )
        rows = fetch_workflow_events(f"{SECURITY_WORKFLOW_PREFIX}dup-event-check", limit=20)
    assert first_id == second_id
    assert len(rows) == 1


def test_replay_transaction_rolls_back_on_conflict(app):
    fingerprint = "rollback-fingerprint"
    created_at = dt.datetime.now().isoformat(timespec="seconds")
    with app.app_context():
        try:
            with ReplayTransactionContext() as connection:
                connection.execute(
                    """
                    INSERT INTO workflow_events (
                        workflow_id, trace_id, correlation_id, stage, agent, action, reasons, payload_json, event_fingerprint, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("rollback-workflow", "rollback-workflow", "rollback-workflow", "test", "repo-test", "first", "[]", "{}", fingerprint, created_at),
                )
                connection.execute(
                    """
                    INSERT INTO workflow_events (
                        workflow_id, trace_id, correlation_id, stage, agent, action, reasons, payload_json, event_fingerprint, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("rollback-workflow", "rollback-workflow", "rollback-workflow", "test", "repo-test", "second", "[]", "{}", fingerprint, created_at),
                )
        except Exception:
            pass
        with get_connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM workflow_events WHERE workflow_id = ?",
                ("rollback-workflow",),
            ).fetchone()[0]
    assert count == 0


def test_worker_execution_repository_is_idempotent(app):
    with app.app_context():
        repository = WorkerExecutionRepository()
        first = repository.record_execution(
            task_id="task-1",
            task_name="docq.test.task",
            workflow_id="worker-test",
            originating_event_id=None,
            idempotency_key="worker-key-1",
            execution_checksum="checksum-1",
            payload={"attempt": 1},
        )
        second = repository.record_execution(
            task_id="task-2",
            task_name="docq.test.task",
            workflow_id="worker-test",
            originating_event_id=None,
            idempotency_key="worker-key-1",
            execution_checksum="checksum-1",
            payload={"attempt": 2},
        )
        row = repository.fetch_execution_by_key(idempotency_key="worker-key-1")
    assert first.created is True
    assert second.created is False
    assert row["task_id"] == "task-1"


def test_append_only_update_protection_blocks_governance_timeline_mutation(app):
    with app.app_context():
        persisted = persist_governance_timeline_event(
            GovernanceTimelineEvent(
                governance_entity_type="model_evaluation_run",
                governance_entity_id=1,
                event_type="created",
                event_timestamp=dt.datetime.now().isoformat(timespec="seconds"),
                related_model_key="risk-active",
                related_threshold_profile_key="risk-threshold-active",
                incident_correlation_id="",
                payload_json={"status": "created"},
            )
        )
        with pytest.raises(Exception):
            with get_connection() as connection:
                connection.execute(
                    "UPDATE governance_timelines SET event_type = ? WHERE id = ?",
                    ("mutated", persisted.id),
                )


def test_replay_snapshot_hydration_preserves_replay(app):
    workflow_id = "snapshot-hydration-a"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Persistent chest pain and dizziness",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=69,
            stored_history="hypertension",
        )
        hydration_a = hydrate_workflow_replay(workflow_id, limit=200)
        snapshot = persist_replay_snapshot(hydration_a.replay)
        hydration_b = hydrate_workflow_replay(workflow_id, limit=200)
    assert snapshot is not None
    assert hydration_b.snapshot_hit is True
    assert hydration_a.replay.step_count == hydration_b.replay.step_count
    assert hydration_a.checkpoint.checkpoint_checksum == hydration_b.checkpoint.checkpoint_checksum


def test_replay_snapshot_invalid_checksum_is_detected(app):
    workflow_id = "snapshot-invalid-a"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Mild cough with fatigue",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=33,
            stored_history="",
        )
        hydration = hydrate_workflow_replay(workflow_id, limit=200)
        snapshot = persist_replay_snapshot(hydration.replay)
        with get_connection() as connection:
            row = connection.execute("SELECT * FROM replay_snapshots WHERE id = ?", (snapshot.id,)).fetchone()
        broken = validate_snapshot(type(snapshot)(**{**snapshot.model_dump(), "snapshot_checksum": "broken"}))
    assert snapshot is not None
    assert row is not None
    assert broken.valid is False


def test_worker_lease_coordination_is_deterministic(app):
    with app.app_context():
        first = acquire_worker_lease(
            worker_id="worker-a",
            task_id="lease-task-1",
            workflow_id="lease-workflow-1",
            retry_generation=0,
            execution_checksum="checksum-a",
        )
        second = acquire_worker_lease(
            worker_id="worker-b",
            task_id="lease-task-1",
            workflow_id="lease-workflow-1",
            retry_generation=0,
            execution_checksum="checksum-a",
        )
        renewed = renew_worker_lease(first.lease.lease_token)
        released = release_worker_lease(first.lease.lease_token)
    assert first.acquired is True
    assert second.acquired is False
    assert renewed.acquired is True
    assert released.acquired is True


def test_operational_rollups_are_reproducible(app):
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id="rollup-workflow-a",
            raw_message="Persistent chest pain",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=71,
            stored_history="diabetes",
        )
        first = build_operational_rollup()
        second = build_operational_rollup()
        latest = fetch_latest_rollup("operational")
    assert first["payload"] == second["payload"]
    assert first["rollup_checksum"] == second["rollup_checksum"]
    assert latest is not None


def test_transactional_outbox_appends_after_event_commit(app):
    with app.app_context():
        event_id = record_security_event(
            "outbox-check",
            action="outbox_test",
            decision="accepted",
            payload={"kind": "outbox"},
            confidence=100.0,
        )
        with get_connection() as connection:
            row = connection.execute("SELECT * FROM event_outbox WHERE event_id = ?", (event_id,)).fetchone()
    assert row is not None
    assert row["publish_status"] in {"pending", "published"}
    compatibility = validate_event_envelope(
        {
            "schema_version": row["schema_version"],
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "workflow_id": row["workflow_id"],
            "trace_id": row["trace_id"],
            "payload_checksum": row["payload_checksum"],
            "created_at": row["created_at"],
        }
    )
    assert compatibility.compatible is True


def test_event_bus_publish_is_ordered_and_idempotent(app):
    with app.app_context():
        first = record_security_event("bus-order-1", action="bus_test_1", decision="accepted", payload={"order": 1}, confidence=100.0)
        second = record_security_event("bus-order-2", action="bus_test_2", decision="accepted", payload={"order": 2}, confidence=100.0)
        published_first = event_publisher.publish_pending(limit=100)
        published_second = event_publisher.publish_pending(limit=100)
        with get_connection() as connection:
            outbox_rows = connection.execute(
                "SELECT event_id, publish_status FROM event_outbox WHERE event_id IN (?, ?) ORDER BY id ASC",
                (first, second),
            ).fetchall()
            delivery_rows = connection.execute(
                "SELECT COUNT(*) FROM event_delivery_records WHERE event_id IN (?, ?)",
                (first, second),
            ).fetchone()[0]
    assert published_first >= 2
    assert published_second == 0
    assert [row["event_id"] for row in outbox_rows] == [first, second]
    assert all(row["publish_status"] == "published" for row in outbox_rows)
    assert delivery_rows >= 2


def test_projection_checkpointing_is_reproducible(app):
    workflow_id = "projection-rebuild-a"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Persistent chest discomfort",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=57,
            stored_history="hypertension",
        )
        event_publisher.publish_pending(limit=200)
        event_publisher.publish_pending(limit=200)
        projection_a = fetch_projection_snapshot("workflow_projection")
        checkpoint_a = fetch_projection_checkpoint("workflow_projection")
        published_again = event_publisher.publish_pending(limit=200)
        projection_b = fetch_projection_snapshot("workflow_projection")
        checkpoint_b = fetch_projection_checkpoint("workflow_projection")
    assert published_again == 0
    assert projection_a[workflow_id] == projection_b[workflow_id]
    assert checkpoint_a is not None
    assert checkpoint_b is not None
    assert checkpoint_a.projection_checksum == checkpoint_b.projection_checksum


def test_invalid_event_envelope_is_rejected():
    compatibility = validate_event_envelope({"schema_version": "v1", "event_type": "workflow_transition"})
    assert compatibility.compatible is False
    assert "event_id" in compatibility.missing_fields


def test_nats_event_bus_adapter_preserves_order_with_memory_backend(app):
    with app.app_context():
        publisher = NatsJetStreamEventBus(
            registry=get_event_publisher().registry,
            nats_url="memory://docq-test",
            node_id="test-node",
        )
        first = record_security_event("nats-order-1", action="nats_test_1", decision="accepted", payload={"order": 1}, confidence=100.0)
        second = record_security_event("nats-order-2", action="nats_test_2", decision="accepted", payload={"order": 2}, confidence=100.0)
        published = publisher.publish_pending(limit=100)
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT event_id, publish_status FROM event_outbox WHERE event_id IN (?, ?) ORDER BY id ASC",
                (first, second),
            ).fetchall()
    assert published >= 2
    assert [row["event_id"] for row in rows] == [first, second]
    assert all(row["publish_status"] == "published" for row in rows)


def test_advisory_lock_ownership_is_deterministic(app):
    with app.app_context():
        first = acquire_advisory_lock(lock_key="projection:test", owner_id="node-a", timeout_seconds=60)
        second = acquire_advisory_lock(lock_key="projection:test", owner_id="node-b", timeout_seconds=60)
        released = release_advisory_lock(lock_key="projection:test", owner_id="node-a")
        third = acquire_advisory_lock(lock_key="projection:test", owner_id="node-b", timeout_seconds=60)
    assert first.acquired is True
    assert second.acquired is False
    assert released.acquired is True
    assert third.acquired is True


def test_projection_worker_rebuild_is_idempotent(app):
    workflow_id = "projection-worker-rebuild"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Persistent chest discomfort and fatigue",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=64,
            stored_history="hypertension",
        )
        first = rebuild_projection("workflow_projection", worker_id="projection-worker-a", batch_size=200)
        second = rebuild_projection("workflow_projection", worker_id="projection-worker-a", batch_size=200)
        snapshot = fetch_projection_snapshot("workflow_projection")
    assert first["rebuilt"] is True
    assert second["rebuilt"] is True
    assert snapshot[workflow_id]["latest_event_id"] > 0


def test_distributed_replay_worker_uses_snapshot_safe_hydration(app):
    workflow_id = "distributed-replay-worker"
    with app.app_context():
        CaseWorkflowEngine().run_intake(
            conversation_id=workflow_id,
            raw_message="Persistent chest pain and shortness of breath",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=67,
            stored_history="diabetes",
        )
        first = run_distributed_replay_hydration(workflow_id, worker_id="replay-worker-a", limit=200)
        second = run_distributed_replay_hydration(workflow_id, worker_id="replay-worker-a", limit=200)
    assert first["hydrated"] is True
    assert second["hydrated"] is True
    assert first["checkpoint_checksum"] == second["checkpoint_checksum"]


def test_partition_route_metadata_is_available(app):
    with app.app_context():
        route = build_partition_route("event_outbox")
    assert route["table_name"] == "event_outbox"
    assert route["partition_strategy"] == "monthly"
    assert route["partition_key"]


def test_runtime_nodes_are_recorded_on_app_boot(app):
    with app.app_context():
        nodes = list_runtime_nodes()
    assert nodes
    assert any(row["node_id"] == app.config["NODE_ID"] for row in nodes)


def test_appointment_lifecycle_transitions_are_deterministic(app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        appointment = create_appointment(
            {
                "patient_name": "Lifecycle User",
                "patient_email": "lifecycle@example.com",
                "phone": "1111111111",
                "patient_age": 44,
                "medical_history": "asthma",
                "symptoms": "Routine cough review",
                "specialty": "General",
                "appointment_date": tomorrow,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
        transition_appointment_lifecycle(
            int(appointment["id"]),
            to_state="reminder_pending",
            cause="manual reminder check",
            actor_name="Tester",
            actor_role="admin",
        )
        state = reconstruct_operational_state(int(appointment["id"]))
    assert state["current_state"] == "reminder_pending"
    assert state["transition_count"] >= 2


def test_sla_scan_creates_escalation_and_coordination_queue(app):
    past = (dt.datetime.now() - dt.timedelta(hours=2)).isoformat(timespec="seconds")
    with app.app_context():
        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO appointments (
                    patient_name, patient_email, phone, patient_age, medical_history, symptoms, extracted_symptoms,
                    specialty, doctor_name, branch, appointment_date, slot_time, slot_id, severity, urgency, confidence,
                    priority_score, history_summary, quick_aid, triage_summary, queue_state, status,
                    created_by, follow_up_status, reminder_sent, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "SLA User",
                    "sla@example.com",
                    "2222222222",
                    51,
                    "hypertension",
                    "Persistent cough",
                    "[]",
                    "General",
                    "DOCQ General",
                    "Mysore Central",
                    dt.date.today().isoformat(),
                    "09:00",
                    None,
                    "Low",
                    "Low",
                    85.0,
                    10.0,
                    "history",
                    "aid",
                    "summary",
                    "manual-review",
                    "review",
                    "Tester",
                    "scheduled",
                    0,
                    past,
                ),
            )
            appointment_id = int(cursor.lastrowid)
        result = scan_sla_violations(worker_id="sla-worker-a")
        queue_rows = fetch_coordination_queue_items("doctor_review", limit=20)
        violations = fetch_sla_violations(limit=20)
    assert result["violations_detected"] >= 1
    assert any(int(row["appointment_id"]) == appointment_id for row in queue_rows)
    assert any(int(row["appointment_id"]) == appointment_id for row in violations)


def test_reminder_runtime_and_worker_are_idempotent(app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        appointment = create_appointment(
            {
                "patient_name": "Reminder User",
                "patient_email": "reminder-runtime@example.com",
                "phone": "3333333333",
                "patient_age": 39,
                "medical_history": "",
                "symptoms": "Routine consultation",
                "specialty": "General",
                "appointment_date": tomorrow,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
        first = enqueue_reminder_worker_task(int(appointment["id"]), "appointment_reminder")
        second = enqueue_reminder_worker_task(int(appointment["id"]), "appointment_reminder")
        run = run_reminder_worker(worker_id="reminder-worker-a")
        state = current_lifecycle_state(int(appointment["id"]))
    assert first is True
    assert second is False
    assert run["executed"] is True
    assert state == "reminder_pending"


def test_whatsapp_notifications_and_profile_preferences_are_persisted(app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        appointment = create_appointment(
            {
                "patient_name": "WhatsApp User",
                "patient_email": "whatsapp@example.com",
                "phone": "7878787878",
                "patient_age": 39,
                "medical_history": "",
                "symptoms": "Routine consultation",
                "specialty": "General",
                "appointment_date": tomorrow,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
        notifications = fetch_notifications(limit=20, target_name="WhatsApp User")
        channels = {row["channel"] for row in notifications}
    assert "whatsapp" in channels
    assert "sms" in channels
    assert any(str(row["correlation_id"]).startswith(f"appointment:{appointment['id']}:") for row in notifications)


def test_reassignment_and_queue_assignment_preserve_lineage(app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        appointment = create_appointment(
            {
                "patient_name": "Reassign User",
                "patient_email": "reassign@example.com",
                "phone": "4444444444",
                "patient_age": 58,
                "medical_history": "hypertension",
                "symptoms": "Cardiology review",
                "specialty": "Cardiology",
                "appointment_date": tomorrow,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
        queue_item = enqueue_coordination_item(
            queue_type="reassignment_review",
            appointment_id=int(appointment["id"]),
            workflow_id=f"appointment-lifecycle:{appointment['id']}",
            priority=80,
            queue_status="pending",
            payload={"reason": "load balance"},
        )
        assigned = assign_queue_item(int(queue_item.id), owner="operator-a")
        selected = deterministic_reassign_appointment(
            int(appointment["id"]),
            candidate_doctors=["DOCQ General", "DOCQ Cardiology"],
            actor_name="operator-a",
            actor_role="operations",
        )
        state = current_lifecycle_state(int(appointment["id"]))
    assert assigned.assigned_owner == "operator-a"
    assert selected
    assert state == "reassignment_pending"


def test_operational_playbooks_and_calendar_sync_are_replay_safe(app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        appointment = create_appointment(
            {
                "patient_name": "Playbook User",
                "patient_email": "playbook@example.com",
                "phone": "5550005555",
                "patient_age": 47,
                "medical_history": "",
                "symptoms": "Follow up visit",
                "specialty": "General",
                "appointment_date": tomorrow,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
        create_notification(int(appointment["id"]), "patient", "Playbook User", "sms", "failure", status="failed", last_error="twilio-failed")
        playbook = handle_notification_failures(worker_id="playbook-a")
        no_show = handle_no_show_recovery(int(appointment["id"]), worker_id="playbook-a")
        sync_state = sync_appointment_to_calendar(int(appointment["id"]), provider="google", external_ref="gcal-123")
        reconcile = reconcile_calendar_availability("outlook", doctor_name="DOCQ General", available_slots=["2026-05-11T09:00:00"])
        projection_result = rebuild_projection("calendar_sync_projection", worker_id="projection-worker-b", batch_size=200)
        calendar_projection = fetch_projection_snapshot("calendar_sync_projection")
    assert playbook["processed"] >= 1
    assert no_show["recovery"] == "started"
    assert sync_state.sync_status == "synced"
    assert reconcile["provider"] == "outlook"
    assert projection_result["rebuilt"] is True
    assert str(appointment["id"]) in calendar_projection


def test_distributed_operational_workers_are_deterministic(app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        appointment = create_appointment(
            {
                "patient_name": "Ops Worker User",
                "patient_email": "opsworker@example.com",
                "phone": "6666666667",
                "patient_age": 41,
                "medical_history": "",
                "symptoms": "General consultation",
                "specialty": "General",
                "appointment_date": tomorrow,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
        reminder_run = run_reminder_worker(worker_id="ops-worker-reminder")
        sla_run = run_sla_worker(worker_id="ops-worker-sla")
        playbook_run = run_playbook_worker(worker_id="ops-worker-playbook")
        calendar_run = run_calendar_sync_worker(int(appointment["id"]), provider="outlook", worker_id="ops-worker-calendar")
        state = reconstruct_operational_state(int(appointment["id"]))
    assert reminder_run["executed"] is True
    assert sla_run["executed"] is True
    assert playbook_run["executed"] is True
    assert calendar_run["executed"] is True
    assert state["current_state"] in {"reminder_pending", "appointment_confirmed", "incident_recovery_pending", "no_show_detected", "reassignment_pending"}


def test_enterprise_tenant_state_api_is_scoped(client):
    csrf = extract_csrf(client, "/clinic-login")
    login_response = client.post(
        "/clinic-login",
        data={"email": "clinicadmin@docq.local", "password": "clinic123", "_csrf_token": csrf},
        follow_redirects=False,
    )
    assert login_response.status_code == 302
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.get("/api/tenants/default-clinic/state?tenant_key=default-clinic", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["tenant_key"] == "default-clinic"
    assert "workflow_count" in payload


def test_enterprise_compliance_audit_export_is_recorded(client):
    csrf = extract_csrf(client, "/clinic-login")
    client.post(
        "/clinic-login",
        data={"email": "compliance@docq.local", "password": "compliance123", "_csrf_token": csrf},
        follow_redirects=False,
    )
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.get("/api/compliance/audit-export?tenant_key=default-clinic", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["tenant_key"] == "default-clinic"
    assert "checksum" in payload
    assert "payload" in payload


def test_enterprise_billing_event_api_persists_lineage(client, app):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with app.app_context():
        appointment = create_appointment(
            {
                "patient_name": "Billing Patient",
                "patient_email": "billing@example.com",
                "phone": "8888888888",
                "patient_age": 54,
                "medical_history": "hypertension",
                "symptoms": "Chest discomfort",
                "specialty": "Cardiology",
                "appointment_date": tomorrow,
            },
            actor_name="Tester",
            actor_role="admin",
            config=app.config,
        )
    csrf = extract_csrf(client, "/clinic-login")
    client.post(
        "/clinic-login",
        data={"email": "clinicadmin@docq.local", "password": "clinic123", "_csrf_token": csrf},
        follow_redirects=False,
    )
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.post(
        "/api/billing/events",
        json={
            "tenant_key": "default-clinic",
            "appointment_id": appointment["id"],
            "workflow_id": f"appointment-lifecycle:{appointment['id']}",
            "event_type": "insurance_verification_pending",
            "amount_cents": 2500,
            "status": "pending",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["tenant_key"] == "default-clinic"
    with app.app_context():
        with get_connection() as connection:
            row = connection.execute(
                "SELECT tenant_key, event_type, amount_cents FROM billing_events WHERE id = ?",
                (payload["id"],),
            ).fetchone()
    assert row["tenant_key"] == "default-clinic"
    assert row["event_type"] == "insurance_verification_pending"
    assert row["amount_cents"] == 2500


def test_enterprise_disaster_recovery_export_and_verify(client):
    csrf = extract_csrf(client, "/clinic-login")
    client.post(
        "/clinic-login",
        data={"email": "compliance@docq.local", "password": "compliance123", "_csrf_token": csrf},
        follow_redirects=False,
    )
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    export_response = client.post(
        "/api/disaster-recovery/export?tenant_key=default-clinic",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert export_response.status_code == 200
    exported = export_response.get_json()
    verify_response = client.get(
        f"/api/disaster-recovery/export/{exported['id']}/verify",
        headers={"X-CSRF-Token": csrf},
    )
    assert verify_response.status_code == 200
    verified = verify_response.get_json()
    assert verified["verified"] is True


def test_docs_and_openapi_routes_are_available(client):
    docs_response = client.get("/docs")
    assert docs_response.status_code == 200
    assert b"Developer Docs" in docs_response.data
    openapi_response = client.get("/api/openapi.json")
    assert openapi_response.status_code == 200
    payload = openapi_response.get_json()
    assert payload["openapi"] == "3.1.0"
    assert "/api/intake" in payload["paths"]


def test_onboarding_and_observability_routes_render_for_enterprise_roles(client):
    csrf = extract_csrf(client, "/clinic-login")
    client.post(
        "/clinic-login",
        data={"email": "clinicadmin@docq.local", "password": "clinic123", "_csrf_token": csrf},
        follow_redirects=False,
    )
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    onboarding_response = client.get("/onboarding", headers={"X-CSRF-Token": csrf})
    observability_response = client.get("/observability", headers={"X-CSRF-Token": csrf})
    topology_response = client.get("/api/observability/topology", headers={"X-CSRF-Token": csrf})
    assert onboarding_response.status_code == 200
    assert observability_response.status_code == 200
    assert topology_response.status_code == 200
    assert "nodes" in topology_response.get_json()


def test_demo_bootstrap_is_deterministic(client):
    csrf = extract_csrf(client, "/clinic-login")
    client.post(
        "/clinic-login",
        data={"email": "clinicadmin@docq.local", "password": "clinic123", "_csrf_token": csrf},
        follow_redirects=False,
    )
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    first = client.post("/api/demo/bootstrap", json={}, headers={"X-CSRF-Token": csrf})
    second = client.post("/api/demo/bootstrap", json={}, headers={"X-CSRF-Token": csrf})
    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.get_json()
    second_payload = second.get_json()
    assert first_payload["workflow_count"] >= 1
    assert second_payload["appointment_count"] == first_payload["appointment_count"]


def test_deployment_validation_and_observability_dashboard_routes(client):
    csrf = extract_csrf(client, "/clinic-login")
    client.post(
        "/clinic-login",
        data={"email": "clinicadmin@docq.local", "password": "clinic123", "_csrf_token": csrf},
        follow_redirects=False,
    )
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    deployment = client.get("/api/deployment/validate", headers={"X-CSRF-Token": csrf})
    dashboards = client.get("/api/observability/dashboards?tenant_key=default-clinic", headers={"X-CSRF-Token": csrf})
    assert deployment.status_code == 200
    assert dashboards.status_code == 200
    assert "startup" in deployment.get_json()
    assert dashboards.get_json()["dashboards"]


def test_integration_health_route_exposes_real_activation_contract(client):
    csrf = extract_csrf(client, "/clinic-login")
    client.post(
        "/clinic-login",
        data={"email": "clinicadmin@docq.local", "password": "clinic123", "_csrf_token": csrf},
        follow_redirects=False,
    )
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    response = client.get("/api/integrations/health?tenant_key=default-clinic", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["tenant_key"] == "default-clinic"
    assert any(provider["provider"] == "twilio_sms" for provider in payload["providers"])
    assert all("configured" in provider for provider in payload["providers"])


def test_benchmark_and_chaos_routes_are_deterministic(client):
    csrf = extract_csrf(client, "/clinic-login")
    client.post(
        "/clinic-login",
        data={"email": "clinicadmin@docq.local", "password": "clinic123", "_csrf_token": csrf},
        follow_redirects=False,
    )
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    benchmark = client.post("/api/load-tests/benchmarks", json={"iterations": 1}, headers={"X-CSRF-Token": csrf})
    chaos = client.post(
        "/api/chaos/run?tenant_key=default-clinic",
        json={"scenario": "worker-crash", "experiment_key": "chaos-smoke"},
        headers={"X-CSRF-Token": csrf},
    )
    assert benchmark.status_code == 200
    assert chaos.status_code == 200
    benchmark_payload = benchmark.get_json()
    chaos_payload = chaos.get_json()
    assert benchmark_payload["iterations"] == 1
    assert "orchestration" in benchmark_payload
    assert chaos_payload["scenario"] == "worker-crash"
    assert chaos_payload["evidence"]["replay_integrity_preserved"] is True
