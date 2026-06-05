from __future__ import annotations

import datetime as dt
import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, flash, g, jsonify, redirect, render_template, request, session, stream_with_context, url_for

from .appointments import build_drift_detection_summary, build_patient_workspace_context, build_workflow_event_record, build_workflow_model_diff, build_workflow_replay, build_workflow_replay_diff, cancel_appointment, create_appointment, escalate_appointment_priority, escalate_stale_reviews, fetch_appointments, fetch_audit_logs, fetch_available_dates, fetch_care_plans, fetch_doctor_users, fetch_due_reminders, fetch_latest_prescription, fetch_monitoring_checkins, fetch_patient_appointments, fetch_notifications_for_appointment, fetch_prescriptions, fetch_report_analyses, fetch_workflow_events, get_appointment, get_patient_profile, init_db, link_patient_profile_to_user, log_action, mark_reminder_delivery, reassign_appointment_doctor, recommend_doctor_matches, record_automation_run, record_patient_vitals, record_report_analysis, record_security_event, reschedule_appointment, save_care_plan, save_clinical_diary, save_prescription_record, seed_slots, seed_slots_for_doctor, update_appointment_status, update_doctor_notes, update_doctor_user, update_report_review, upsert_patient_profile
from .analytics import build_operational_analytics
from .api_docs import build_docs_context, build_openapi_spec
from .auth import consume_auth_token, create_user, get_user_by_email, get_user_by_id, inject_globals, is_safe_redirect_target, issue_auth_token, load_current_user, login_required, login_user, mark_user_email_verified, role_required, seed_users, update_user_password, validate_csrf, verify_password
from .billing_runtime import record_billing_event
from .chaos_runtime import run_chaos_experiment
from .clinical_questionnaires import next_question
from .compliance import export_audit_bundle, log_sensitive_access, mask_sensitive_value
from .config import Config
from .constants import SPECIALTY_LABELS
from .contracts import EVENT_SCHEMA_VERSION, WorkflowMetricsSummary
from .dashboard import build_admin_event_feed, build_admin_notification_feed, build_admin_runtime_snapshot, build_admin_workflow_feed, build_dashboard_metrics, build_doctor_metrics, build_incident_console_snapshot, build_patient_continuity_snapshot, build_schedule_governance_snapshot, build_workflow_console_snapshot
from .demo_seed import bootstrap_demo_environment
from .deployment_runtime import build_deployment_health_panel
from .department_classification import classify_department
from .event_bus import bootstrap_default_consumers, configure_event_publisher, get_event_publisher
from .disaster_recovery import export_recovery_bundle, verify_recovery_bundle
from .governance_runtime import run_continuous_governance
from .human_coordination import assign_queue_item
from .integrations.adapters import google_calendar_adapter, outlook_calendar_adapter, sendgrid_email_adapter, slack_webhook_adapter, twilio_sms_adapter, twilio_whatsapp_adapter, webhook_delivery_adapter
from .load_testing import run_benchmark_suite
from .ml import init_models, normalize_specialty, train_models
from .model_evaluation import get_model_evaluation_diff, get_model_evaluation_drift, get_model_evaluation_promotion_gate, get_model_evaluation_results, get_model_evaluation_run, list_model_evaluations, run_offline_model_evaluation
from .notifications import notify_prescription_ready, process_notification_queue, retry_notification, send_due_reminders
from .observability import begin_request_trace, finalize_request_trace, metrics_registry
from .pydantic_compat import model_dump
from .production_db import check_database_readiness
from .report_analysis import analyze_report_text, extract_text_from_upload, save_report_file
from .scheduling_engine import build_department_calendar, build_doctor_calendar, compact_available_dates, doctor_workload
from .tenancy import fetch_tenant_summary, user_has_tenant_access
from .runtime_topology import assign_consumer_ownership, list_runtime_nodes, record_node_heartbeat
from .workflow_engine import CaseWorkflowEngine
from .appointments import build_governance_recommendation_contract, build_governance_timeline_event_contract, build_rollout_profile_contract, fetch_governance_recommendation, fetch_governance_recommendations, fetch_governance_timeline, fetch_rollout_profiles


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent.parent / "templates"),
        static_folder=str(Path(__file__).resolve().parent.parent / "static"),
    )
    config = Config()
    app.config.from_mapping(config.__dict__)
    if test_config:
        app.config.update(test_config)
        if "DB_PATH" in test_config and "DATABASE_URL" not in test_config:
            app.config["DATABASE_URL"] = f"sqlite:///{app.config['DB_PATH']}"
            app.config["SQLALCHEMY_DATABASE_URI"] = app.config["DATABASE_URL"]
    app.logger.setLevel(logging.INFO)
    app.config["MODEL_DIR"].mkdir(exist_ok=True)
    app.permanent_session_lifetime = dt.timedelta(minutes=int(app.config.get("SESSION_TTL_MINUTES", 120)))
    rate_limit_hits: dict[tuple[str, str], list[float]] = defaultdict(list)
    bootstrap_default_consumers()
    publisher = configure_event_publisher(
        backend=str(app.config.get("EVENT_BUS_BACKEND", "inprocess")),
        nats_url=str(app.config.get("NATS_URL", "memory://docq")),
        node_id=str(app.config.get("NODE_ID", "docq-node")),
    )
    admin_ops_roles = (
        "admin",
        "auditor",
        "operations",
        "operations_manager",
        "hospital_admin",
        "clinic_admin",
        "governance_reviewer",
        "compliance_officer",
        "department_supervisor",
    )

    def _build_whatsapp_sandbox_onboarding() -> dict[str, object] | None:
        whatsapp_from = str(app.config.get("TWILIO_WHATSAPP_FROM", "") or "").strip()
        join_code = str(app.config.get("TWILIO_WHATSAPP_SANDBOX_JOIN_CODE", "") or "").strip()
        if not whatsapp_from or not join_code:
            return None
        if whatsapp_from != "whatsapp:+14155238886":
            return None
        sandbox_number = "14155238886"
        join_message = f"join {join_code}"
        return {
            "required": True,
            "provider": "twilio-whatsapp-sandbox",
            "sandbox_number": f"+{sandbox_number}",
            "join_code": join_code,
            "join_message": join_message,
            "join_url": f"https://wa.me/{sandbox_number}?text={quote(join_message)}",
        }

    def _can_access_appointment(appointment) -> bool:
        if appointment is None or g.user is None:
            return False
        role = str(g.user["role"])
        if role in admin_ops_roles or role in {"admin", "operations", "clinic_admin", "hospital_admin"}:
            return True
        if role in {"doctor", "clinician"}:
            return str(appointment["doctor_name"] or "") == str(g.user.get("doctor_name") or "")
        if role == "patient":
            return str(appointment["patient_email"] or "").lower() == str(g.user.get("email") or "").lower()
        return False

    def _process_report_upload(appointment_id: int) -> dict[str, object]:
        appointment = get_appointment(appointment_id)
        if appointment is None:
            raise ValueError("Appointment not found.")
        if not _can_access_appointment(appointment):
            raise PermissionError("You do not have access to this appointment.")
        report_file = request.files.get("report_file")
        raw_text = str(request.form.get("report_text") or (request.get_json(silent=True) or {}).get("report_text", "")).strip()
        extracted = extract_text_from_upload(report_file, raw_text=raw_text)
        if extracted["ocr_status"] in {"no_file", "unsupported_file_type"}:
            raise ValueError("Upload a supported report file or provide report text.")
        stored_name = "submitted-text.txt"
        if report_file is not None and report_file.filename:
            upload_root = Path(app.config.get("REPORT_UPLOAD_DIR") or (Path(app.config["DB_PATH"]).parent / "uploads" / "reports"))
            stored_name = save_report_file(report_file, upload_root / str(appointment_id))
        analysis = analyze_report_text(str(extracted["text"]))
        report_id = record_report_analysis(
            appointment_id=appointment_id,
            patient_name=str(appointment["patient_name"]),
            report_type=str(analysis["report_type"]),
            file_name=stored_name,
            ocr_status=str(extracted["ocr_status"]),
            lab_values=dict(analysis["lab_values"]),
            abnormal_findings=list(analysis["abnormal_findings"]),
        )
        log_action(
            g.user["name"],
            g.user["role"],
            "upload-report",
            "appointment",
            appointment_id,
            f"report {stored_name} processed as {analysis['report_type']}",
        )
        return {
            "id": report_id,
            "appointment_id": appointment_id,
            "file_name": stored_name,
            "ocr_status": extracted["ocr_status"],
            "extraction_method": extracted["extraction_method"],
            **analysis,
        }

    def _is_authorized_cron_request() -> bool:
        cron_secret = str(app.config.get("CRON_SECRET") or "").strip()
        if cron_secret:
            return request.headers.get("Authorization", "") == f"Bearer {cron_secret}"
        if bool(app.config.get("TESTING")):
            return True
        return str(app.config.get("ENV_NAME", "development")).lower() != "production"

    @app.before_request
    def _load_user() -> None:
        load_current_user()

    @app.before_request
    def _request_runtime_guards():
        begin_request_trace()
        get_event_publisher().publish_pending(limit=50)
        record_node_heartbeat(
            node_id=str(app.config.get("NODE_ID", "docq-node")),
            stream_generation=1,
            metadata={"event_bus_backend": get_event_publisher().describe().get("backend", "unknown")},
        )
        if app.config.get("MAX_CONTENT_LENGTH") and request.content_length and request.content_length > int(app.config["MAX_CONTENT_LENGTH"]):
            return jsonify({"error": "request body too large"}), 413
        if app.config.get("ENABLE_RATE_LIMITS") and not app.config.get("TESTING") and request.endpoint in {"intake_api", "login", "clinic_login", "doctor_login", "patient_login"}:
            bucket = (request.remote_addr or "unknown", request.endpoint)
            now = time.time()
            window = 60.0
            rate_limit_hits[bucket] = [ts for ts in rate_limit_hits[bucket] if now - ts < window]
            limit = 30 if request.endpoint == "intake_api" else 20
            if len(rate_limit_hits[bucket]) >= limit:
                metrics_registry.increment("docq_rate_limit_block_total")
                return jsonify({"error": "rate limit exceeded"}), 429
            rate_limit_hits[bucket].append(now)
        return None

    @app.before_request
    def _validate_csrf():
        try:
            validate_csrf()
        except PermissionError as exc:
            record_security_event(
                "csrf-failure",
                action="csrf_validation_failed",
                decision="rejected",
                payload={"path": request.path, "remote_addr": request.remote_addr or "", "request_id": getattr(g, "request_id", "")},
                reasons=[str(exc)],
            )
            if request.is_json:
                return jsonify({"error": str(exc)}), 400
            flash(str(exc), "error")
            return redirect(request.referrer or url_for("access_portal"))
        return None

    @app.after_request
    def _after_request(response: Response):
        return finalize_request_trace(response)

    @app.context_processor
    def _inject_globals():
        return inject_globals()

    LOGIN_ROLE_CONFIG = {
        "unified": {
            "title": "Unified Login",
            "eyebrow": "DOCQ Unified Access",
            "heading": "Sign in once. DOCQ routes your workspace automatically.",
            "description": "Patients, doctors, admins, clinic staff, operations, audit, and governance teams use the same authentication page.",
            "allowed_roles": {
                "admin",
                "receptionist",
                "operations",
                "governance_analyst",
                "auditor",
                "hospital_admin",
                "clinic_admin",
                "governance_reviewer",
                "compliance_officer",
                "operations_manager",
                "department_supervisor",
                "doctor",
                "clinician",
                "patient",
            },
            "demo_accounts": [
                {"label": "Patient Demo", "email": "patient@docq.local", "password": "patient123"},
                {"label": "Doctor Demo", "email": "cardio@docq.local", "password": "doctor123"},
                {"label": "Admin Demo", "email": "admin@docq.local", "password": "admin123"},
            ],
        },
    }

    def _workspace_for_role(role: str) -> str:
        if role == "patient":
            return url_for("intake")
        if role in {"doctor", "clinician"}:
            return url_for("doctor_inbox")
        if role in {"auditor", "governance_analyst", "governance_reviewer", "compliance_officer"}:
            return url_for("admin_dashboard")
        return url_for("dashboard")

    def _login_view(login_kind: str = "unified"):
        config_item = LOGIN_ROLE_CONFIG["unified"]
        if request.method == "POST":
            raw_email = request.form.get("email", "")
            normalized_email = str(raw_email or "").strip().lower()
            user = get_user_by_email(normalized_email)
            password = request.form.get("password", "")
            password_valid = bool(user and verify_password(user["password_hash"], password))
            user_status = str(user["status"] if user and "status" in user.keys() else "active").lower()
            record_security_event(
                f"login-attempt-{login_kind}-{int(time.time() * 1000)}",
                action="login_attempt_diagnostic",
                decision="observed",
                payload={
                    "email": normalized_email,
                    "login_kind": login_kind,
                    "user_found": bool(user),
                    "role": user["role"] if user else "",
                    "status": user_status if user else "",
                    "password_valid": password_valid,
                    "email_verified": bool(user and user["email_verified_at"]),
                    "request_id": getattr(g, "request_id", ""),
                },
                confidence=100.0 if user else 50.0,
            )
            if not user or not password_valid:
                record_security_event(
                    f"failed-login-{int(time.time() * 1000)}",
                    action="login_failed",
                    decision="rejected",
                    payload={"email": normalized_email, "login_kind": login_kind, "request_id": getattr(g, "request_id", "")},
                    reasons=["invalid credentials"],
                )
                flash("Invalid credentials.", "error")
                return render_template("login.html", login_kind="unified", login_config=config_item)
            if user_status != "active":
                record_security_event(
                    f"inactive-login-{int(time.time() * 1000)}",
                    action="login_inactive_account",
                    decision="rejected",
                    payload={"email": normalized_email, "role": user["role"], "status": user_status, "request_id": getattr(g, "request_id", "")},
                    reasons=["inactive account"],
                )
                flash("This DOCQ account is inactive. Contact your administrator.", "error")
                return render_template("login.html", login_kind="unified", login_config=config_item)
            if user["role"] not in config_item["allowed_roles"]:
                record_security_event(
                    f"role-mismatch-{int(time.time() * 1000)}",
                    action="login_role_mismatch",
                    decision="rejected",
                    payload={"email": normalized_email, "role": user["role"], "login_kind": login_kind},
                    reasons=["account type mismatch"],
                )
                flash("Use the correct DOCQ login for this account type.", "error")
                return render_template("login.html", login_kind="unified", login_config=config_item)
            login_user(user)
            record_security_event(
                f"login-success-{user['id']}-{int(time.time() * 1000)}",
                action="login_succeeded",
                decision="accepted",
                payload={"user_id": user["id"], "role": user["role"], "email": user["email"], "request_id": getattr(g, "request_id", "")},
                confidence=100.0,
            )
            next_url = request.args.get("next")
            if is_safe_redirect_target(next_url):
                return redirect(next_url)
            return redirect(_workspace_for_role(str(user["role"])))
        return render_template("login.html", login_kind="unified", login_config=config_item)

    @app.route("/")
    def access_portal():
        return render_template("access.html")

    @app.route("/docs")
    def docs():
        return render_template("docs.html", docs_context=build_docs_context(app))

    @app.route("/api/openapi.json", methods=["GET"])
    def openapi_api():
        return jsonify(build_openapi_spec(app))

    @app.route("/onboarding", methods=["GET"])
    @login_required
    def onboarding():
        tenant_key = g.user.get("tenant_key") if g.user else app.config.get("DEFAULT_TENANT_KEY", "default-clinic")
        readiness = {
            "database_ready": check_database_readiness(app.config["DATABASE_URL"]),
            "tenant_key": tenant_key,
            "runtime_nodes": list_runtime_nodes(),
            "analytics": build_operational_analytics(tenant_key=tenant_key),
        }
        steps = [
            {"title": "Tenant scope", "state": "complete", "detail": f"Bound to {tenant_key}"},
            {"title": "Notifications", "state": "ready", "detail": "SMS, email, webhook, and calendar adapters available"},
            {"title": "Governance", "state": "ready", "detail": "Replay-safe governance and evaluation tooling active"},
            {"title": "Observability", "state": "ready", "detail": "Health, readiness, metrics, topology, and dashboards exposed"},
        ]
        return render_template("onboarding.html", readiness=readiness, steps=steps)

    @app.route("/observability", methods=["GET"])
    @role_required("admin", "auditor", "operations", "operations_manager", "hospital_admin", "clinic_admin")
    def observability():
        tenant_key = g.user.get("tenant_key") if g.user else app.config.get("DEFAULT_TENANT_KEY", "default-clinic")
        observability_context = {
            "tenant_key": tenant_key,
            "runtime_nodes": list_runtime_nodes(),
            "metrics_lines": metrics_registry.render_prometheus().splitlines()[:30],
            "database_ready": check_database_readiness(app.config["DATABASE_URL"]),
            "analytics": build_operational_analytics(tenant_key=tenant_key),
            "deployment": build_deployment_health_panel(app.config, Path(app.config["BASE_DIR"])),
        }
        return render_template("observability.html", observability=observability_context)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        return _login_view("unified")

    @app.route("/clinic-login", methods=["GET", "POST"])
    def clinic_login():
        return _login_view("unified")

    @app.route("/doctor-login", methods=["GET", "POST"])
    def doctor_login():
        return _login_view("unified")

    @app.route("/patient-login", methods=["GET", "POST"])
    def patient_login():
        return _login_view("unified")

    @app.route("/patient-signup", methods=["GET"])
    def patient_signup():
        return render_template("patient_signup.html")

    @app.route("/patient-signup", methods=["POST"])
    def patient_signup_post():
        name = str(request.form.get("name", "")).strip()
        email = str(request.form.get("email", "")).strip().lower()
        password = str(request.form.get("password", "")).strip()
        phone = str(request.form.get("phone", "")).strip()
        gender = str(request.form.get("gender", "")).strip()
        emergency_contact = str(request.form.get("emergency_contact", "")).strip()
        chronic_conditions = str(request.form.get("chronic_conditions", "")).strip()
        allergies = str(request.form.get("allergies", "")).strip()
        raw_age = request.form.get("patient_age")
        patient_age = int(raw_age) if raw_age not in (None, "") else None
        prefers_sms = request.form.get("prefers_sms") == "yes"
        prefers_email = request.form.get("prefers_email") == "yes"
        prefers_whatsapp = request.form.get("prefers_whatsapp") == "yes"
        if not name or not email or len(password) < 8 or not phone:
            flash("Name, email, phone, and a password of at least 8 characters are required.", "error")
            return render_template("patient_signup.html")
        if get_user_by_email(email):
            flash("An account already exists for that email. Please log in instead.", "error")
            return redirect(url_for("login"))
        user_id = create_user(name=name, email=email, password=password, role="patient", phone=phone, email_verified=False)
        issue_auth_token(user_id=user_id, token_type="email_verification", expires_minutes=60 * 24)
        upsert_patient_profile(
            name,
            phone,
            patient_email=email,
            patient_age=patient_age,
            chronic_conditions=chronic_conditions,
            allergies=allergies,
            gender=gender,
            emergency_contact=emergency_contact,
            communication_preferences={
                "sms": prefers_sms,
                "email": prefers_email,
                "whatsapp": prefers_whatsapp,
            },
            linked_user_id=user_id,
            tenant_key=app.config.get("DEFAULT_TENANT_KEY", "default-clinic"),
        )
        link_patient_profile_to_user(user_id=user_id, patient_email=email, phone=phone)
        user = get_user_by_id(user_id)
        login_user(user, remember_email=True)
        record_security_event(
            f"patient-signup-page-{user_id}",
            action="patient_signup_completed",
            decision="accepted",
            payload={"user_id": user_id, "email": email, "phone": phone, "request_id": getattr(g, "request_id", "")},
            confidence=100.0,
        )
        flash("Patient account created. You are now signed in.", "success")
        return redirect(url_for("intake"))

    @app.route("/patient-forgot-password", methods=["GET", "POST"])
    def patient_forgot_password():
        if request.method == "POST":
            email = str(request.form.get("email", "")).strip().lower()
            if not email:
                flash("Enter the email linked to your DOCQ patient account.", "error")
                return render_template("patient_forgot_password.html", reset_token=None)
            user = get_user_by_email(email)
            reset_token = None
            if user and user["role"] == "patient":
                reset_token = issue_auth_token(user_id=int(user["id"]), token_type="password_reset", expires_minutes=30)
                record_security_event(
                    f"patient-password-reset-page-{user['id']}",
                    action="password_reset_requested",
                    decision="accepted",
                    payload={"user_id": int(user["id"]), "email": email, "request_id": getattr(g, "request_id", "")},
                    confidence=100.0,
                )
            if reset_token and app.config.get("ENV_NAME") != "production":
                flash("Reset token issued for development. Use the reset form below.", "success")
            else:
                flash("If the email exists in DOCQ, a reset path has been prepared.", "success")
            return render_template("patient_forgot_password.html", reset_token=reset_token)
        return render_template("patient_forgot_password.html", reset_token=None)

    @app.route("/patient-reset-password/<token>", methods=["GET", "POST"])
    def patient_reset_password(token: str):
        if request.method == "POST":
            password = str(request.form.get("password", "")).strip()
            if len(password) < 8:
                flash("Password must be at least 8 characters.", "error")
                return render_template("patient_reset_password.html", token=token)
            token_row = consume_auth_token(token, "password_reset")
            if token_row is None:
                flash("Reset link is invalid or expired.", "error")
                return redirect(url_for("patient_forgot_password"))
            update_user_password(int(token_row["user_id"]), password)
            record_security_event(
                f"patient-password-reset-complete-{token_row['user_id']}",
                action="password_reset_completed",
                decision="accepted",
                payload={"user_id": int(token_row["user_id"]), "request_id": getattr(g, "request_id", "")},
                confidence=100.0,
            )
            flash("Password updated. Please log in with your new credentials.", "success")
            return redirect(url_for("login"))
        return render_template("patient_reset_password.html", token=token)

    @app.route("/logout")
    def logout():
        remembered_email = session.get("remembered_email", "")
        if g.user:
            record_security_event(
                f"logout-{g.user['id']}-{int(time.time() * 1000)}",
                action="logout",
                decision="accepted",
                payload={"user_id": g.user["id"], "role": g.user["role"], "request_id": getattr(g, "request_id", "")},
                confidence=100.0,
            )
        session.clear()
        if remembered_email:
            session["remembered_email"] = remembered_email
        return redirect(url_for("access_portal"))

    @app.route("/api/auth/signup", methods=["POST"])
    def signup_api():
        payload = request.get_json(silent=True) or {}
        name = str(payload.get("name", "")).strip()
        email = str(payload.get("email", "")).strip().lower()
        password = str(payload.get("password", "")).strip()
        if not name or not email or len(password) < 8:
            return jsonify({"error": "name, email, and a password of at least 8 characters are required."}), 400
        if get_user_by_email(email):
            return jsonify({"error": "account already exists."}), 409
        user_id = create_user(name=name, email=email, password=password, role="patient")
        record_security_event(
            f"signup-{user_id}",
            action="signup_created",
            decision="accepted",
            payload={"user_id": user_id, "email": email, "role": "patient", "request_id": getattr(g, "request_id", "")},
            confidence=100.0,
        )
        return jsonify({"status": "ok", "user_id": user_id}), 201

    @app.route("/api/auth/patient-signup", methods=["POST"])
    def patient_signup_api():
        payload = request.get_json(silent=True) or {}
        name = str(payload.get("name", "")).strip()
        email = str(payload.get("email", "")).strip().lower()
        password = str(payload.get("password", "")).strip()
        phone = str(payload.get("phone", "")).strip()
        gender = str(payload.get("gender", "")).strip()
        emergency_contact = str(payload.get("emergency_contact", "")).strip()
        raw_age = payload.get("patient_age")
        patient_age = int(raw_age) if raw_age not in (None, "") else None
        chronic_conditions = str(payload.get("chronic_conditions", "")).strip()
        allergies = str(payload.get("allergies", "")).strip()
        communication_preferences = {
            "sms": bool(payload.get("prefers_sms", True)),
            "email": bool(payload.get("prefers_email", True)),
            "whatsapp": bool(payload.get("prefers_whatsapp", True)),
        }
        resume_context = payload.get("resume_context") or {}
        workflow_id = str(resume_context.get("workflow_id", session.get("_csrf_token", "guest-session"))).strip()
        if not name or not email or len(password) < 8 or not phone:
            return jsonify({"error": "name, email, password, and phone are required."}), 400
        if get_user_by_email(email):
            return jsonify({"error": "account already exists."}), 409
        user_id = create_user(name=name, email=email, password=password, role="patient", phone=phone, email_verified=False)
        verification_token = issue_auth_token(user_id=user_id, token_type="email_verification", expires_minutes=60 * 24)
        upsert_patient_profile(
            name,
            phone,
            patient_email=email,
            patient_age=patient_age,
            chronic_conditions=chronic_conditions,
            allergies=allergies,
            gender=gender,
            emergency_contact=emergency_contact,
            communication_preferences=communication_preferences,
            linked_user_id=user_id,
            tenant_key=app.config.get("DEFAULT_TENANT_KEY", "default-clinic"),
        )
        link_patient_profile_to_user(user_id=user_id, patient_email=email, phone=phone)
        user = get_user_by_id(user_id)
        login_user(user)
        session["docq_resume_context"] = resume_context
        record_security_event(
            f"patient-signup-{user_id}",
            action="patient_signup_completed",
            decision="accepted",
            payload={
                "user_id": user_id,
                "email": email,
                "phone": phone,
                "workflow_id": workflow_id,
                "resume_context": resume_context,
                "request_id": getattr(g, "request_id", ""),
            },
            confidence=100.0,
        )
        record_security_event(
            f"patient-verification-issued-{user_id}",
            action="patient_email_verification_issued",
            decision="accepted",
            payload={"user_id": user_id, "token_type": "email_verification", "workflow_id": workflow_id},
            confidence=100.0,
        )
        return jsonify(
            {
                "status": "ok",
                "user_id": user_id,
                "verification_token": verification_token,
                "resume_ready": True,
                "workspace_context": _serialize_workspace_context(build_patient_workspace_context(email)),
                "resume_context": resume_context,
                "whatsapp_onboarding": _build_whatsapp_sandbox_onboarding(),
            }
        ), 201

    @app.route("/verify-email/<token>", methods=["GET"])
    def verify_email(token: str):
        token_row = consume_auth_token(token, "email_verification")
        if token_row is None:
            flash("Verification link is invalid or expired.", "error")
            return redirect(url_for("login"))
        mark_user_email_verified(int(token_row["user_id"]))
        record_security_event(
            f"verify-email-{token_row['user_id']}",
            action="email_verified",
            decision="accepted",
            payload={"user_id": int(token_row["user_id"]), "request_id": getattr(g, "request_id", "")},
            confidence=100.0,
        )
        flash("Email verification completed. You can continue with DOCQ.", "success")
        return redirect(url_for("intake"))

    @app.route("/api/auth/request-password-reset", methods=["POST"])
    def request_password_reset_api():
        payload = request.get_json(silent=True) or {}
        email = str(payload.get("email", "")).strip().lower()
        user = get_user_by_email(email)
        if user:
            token = issue_auth_token(user_id=int(user["id"]), token_type="password_reset", expires_minutes=30)
            record_security_event(
                f"password-reset-issued-{user['id']}",
                action="password_reset_requested",
                decision="accepted",
                payload={"user_id": int(user["id"]), "token": token, "request_id": getattr(g, "request_id", "")},
                confidence=100.0,
            )
        return jsonify({"status": "ok"})

    @app.route("/api/auth/reset-password", methods=["POST"])
    def reset_password_api():
        payload = request.get_json(silent=True) or {}
        token = str(payload.get("token", "")).strip()
        password = str(payload.get("password", "")).strip()
        if len(password) < 8 or not token:
            return jsonify({"error": "token and password are required."}), 400
        token_row = consume_auth_token(token, "password_reset")
        if token_row is None:
            return jsonify({"error": "invalid or expired token."}), 400
        update_user_password(int(token_row["user_id"]), password)
        record_security_event(
            f"password-reset-complete-{token_row['user_id']}",
            action="password_reset_completed",
            decision="accepted",
            payload={"user_id": int(token_row["user_id"]), "request_id": getattr(g, "request_id", "")},
            confidence=100.0,
        )
        return jsonify({"status": "ok"})

    @app.route("/intake")
    def intake():
        workspace_context = None
        patient_portal_context = None
        if g.user and g.user.get("role") == "patient":
            workspace_context = build_patient_workspace_context(g.user.get("email", ""))
            patient_portal_context = _build_patient_portal_context("dashboard")
        return render_template("index.html", workspace_context=workspace_context, patient_portal_context=patient_portal_context)

    @app.route("/api/patient/profile", methods=["GET", "POST"])
    @login_required
    @role_required("patient")
    def patient_profile_api():
        if request.method == "GET":
            return jsonify(_serialize_workspace_context(build_patient_workspace_context(g.user.get("email", ""))))
        payload = request.get_json(silent=True) or {}
        profile = get_patient_profile(patient_email=g.user.get("email", ""))
        phone = str(payload.get("phone") or (profile["phone"] if profile else "") or "").strip()
        upsert_patient_profile(
            str(payload.get("patient_name") or g.user.get("name") or "").strip(),
            phone,
            patient_email=g.user.get("email", ""),
            patient_age=int(payload["patient_age"]) if payload.get("patient_age") not in (None, "") else None,
            chronic_conditions=str(payload.get("chronic_conditions", "")).strip(),
            allergies=str(payload.get("allergies", "")).strip(),
            gender=str(payload.get("gender", "")).strip(),
            emergency_contact=str(payload.get("emergency_contact", "")).strip(),
            communication_preferences={
                "sms": bool(payload.get("prefers_sms", True)),
                "email": bool(payload.get("prefers_email", True)),
                "whatsapp": bool(payload.get("prefers_whatsapp", True)),
            },
            linked_user_id=int(g.user["id"]),
            tenant_key=g.user.get("tenant_key"),
        )
        record_security_event(
            f"patient-profile-{g.user['id']}",
            action="patient_profile_updated",
            decision="accepted",
            payload={"user_id": int(g.user["id"]), "request_id": getattr(g, "request_id", "")},
            confidence=100.0,
        )
        return jsonify({"status": "ok", "workspace_context": _serialize_workspace_context(build_patient_workspace_context(g.user.get("email", "")))})

    patient_portal_sections = [
        {"key": "dashboard", "label": "Dashboard", "endpoint": "patient_dashboard"},
        {"key": "appointments", "label": "Appointments", "endpoint": "patient_portal_section"},
        {"key": "doctors", "label": "Doctors", "endpoint": "patient_portal_section"},
        {"key": "reports", "label": "Reports", "endpoint": "patient_portal_section"},
        {"key": "prescriptions", "label": "Prescriptions", "endpoint": "patient_portal_section"},
        {"key": "care-plans", "label": "Care Plans", "endpoint": "patient_portal_section"},
        {"key": "monitoring", "label": "Monitoring", "endpoint": "patient_portal_section"},
        {"key": "timeline", "label": "Medical Timeline", "endpoint": "patient_portal_section"},
        {"key": "profile", "label": "Profile", "endpoint": "patient_portal_section"},
        {"key": "emergency-help", "label": "Emergency Help", "endpoint": "patient_portal_section"},
        {"key": "settings", "label": "Settings", "endpoint": "patient_portal_section"},
    ]
    patient_portal_section_keys = {item["key"] for item in patient_portal_sections}

    def _patient_portal_url(section: dict[str, str]) -> str:
        if section["key"] == "dashboard":
            return url_for("patient_dashboard")
        return url_for("patient_portal_section", section=section["key"])

    def _friendly_patient_priority(urgency: str) -> str:
        normalized = str(urgency or "").lower()
        if normalized == "emergency":
            return "Immediate Care Recommended"
        if normalized in {"high", "urgent"}:
            return "Requires Medical Review"
        if normalized in {"medium", "moderate"}:
            return "Appointment Recommended"
        return "Routine Care"

    def _friendly_patient_status(appointment: dict[str, object] | None) -> str:
        if not appointment:
            return "Ready to Start Care"
        follow_up = str(appointment.get("follow_up_status") or "")
        status = str(appointment.get("status") or "").replace("-", " ").title()
        queue_state = str(appointment.get("queue_state") or "")
        if follow_up and follow_up != "none":
            return "Follow-Up Scheduled" if follow_up == "scheduled" else "Follow-Up Requested"
        if str(appointment.get("appointment_date") or "") >= dt.date.today().isoformat():
            return "Appointment Scheduled"
        if queue_state == "awaiting-doctor":
            return "Awaiting Doctor Review"
        return status or "Care History Available"

    def _safe_json(value: object, default: object) -> object:
        if not value:
            return default
        try:
            return json.loads(str(value))
        except (TypeError, ValueError):
            return default

    def _build_patient_portal_context(active_section: str = "dashboard") -> dict[str, object]:
        workspace_context = build_patient_workspace_context(g.user.get("email", ""))
        profile = workspace_context.get("profile")
        patient_email = str(g.user.get("email", ""))
        phone = str(profile["phone"] or "") if profile else ""
        appointment_rows = fetch_patient_appointments(phone=phone, patient_email=patient_email, limit=80)
        today = dt.date.today().isoformat()
        records: list[dict[str, object]] = []
        prescriptions: list[dict[str, object]] = []
        reports: list[dict[str, object]] = []
        care_plans: list[dict[str, object]] = []
        monitoring: list[dict[str, object]] = []
        timeline: list[dict[str, object]] = []

        if profile:
            timeline.append(
                {
                    "date": str(profile["created_at"] or "")[:10],
                    "title": "Account Created",
                    "details": "Your DOCQ patient profile was created.",
                    "status": "Complete",
                }
            )

        for row in appointment_rows:
            appointment = dict(row)
            appointment_id = int(appointment["id"])
            appointment["priority_label"] = _friendly_patient_priority(str(appointment.get("urgency") or ""))
            appointment["care_status"] = _friendly_patient_status(appointment)
            latest_prescription = fetch_latest_prescription(appointment_id)
            appointment_reports = [dict(item) for item in fetch_report_analyses(appointment_id, limit=10)]
            appointment_care_plans = [dict(item) for item in fetch_care_plans(appointment_id, limit=5)]
            appointment_monitoring = [dict(item) for item in fetch_monitoring_checkins(appointment_id, limit=10)]
            if latest_prescription:
                prescription_item = dict(latest_prescription)
                prescription_item["department"] = appointment.get("specialty", "")
                prescription_item["appointment_date"] = appointment.get("appointment_date", "")
                prescriptions.append(prescription_item)
                timeline.append(
                    {
                        "date": str(prescription_item.get("created_at") or "")[:10],
                        "title": "Prescription Issued",
                        "details": f"{prescription_item.get('doctor_name')} added medication instructions.",
                        "status": str(prescription_item.get("status") or "issued").title(),
                    }
                )
            for report in appointment_reports:
                report["appointment_date"] = appointment.get("appointment_date", "")
                report["doctor_name"] = appointment.get("doctor_name", "")
                reports.append(report)
                timeline.append(
                    {
                        "date": str(report.get("created_at") or "")[:10],
                        "title": "Report Uploaded",
                        "details": f"{report.get('report_type')} report attached to your record.",
                        "status": str(report.get("ocr_status") or "Submitted").replace("_", " ").title(),
                    }
                )
            for plan in appointment_care_plans:
                plan["plan"] = _safe_json(plan.get("plan_json"), {})
                plan["appointment_date"] = appointment.get("appointment_date", "")
                care_plans.append(plan)
            for checkin in appointment_monitoring:
                checkin["appointment_date"] = appointment.get("appointment_date", "")
                monitoring.append(checkin)
            timeline.append(
                {
                    "date": str(appointment.get("appointment_date") or ""),
                    "title": "Appointment Scheduled" if str(appointment.get("appointment_date") or "") >= today else "Consultation Recorded",
                    "details": f"{appointment.get('specialty')} with {appointment.get('doctor_name')}",
                    "status": appointment["care_status"],
                }
            )
            records.append(
                {
                    "appointment": appointment,
                    "prescription": dict(latest_prescription) if latest_prescription else None,
                    "reports": appointment_reports,
                    "care_plans": appointment_care_plans,
                    "monitoring": appointment_monitoring,
                    "notifications": [dict(item) for item in fetch_notifications_for_appointment(appointment_id, limit=5)],
                }
            )

        appointments = [record["appointment"] for record in records]
        upcoming = [item for item in appointments if item["status"] not in {"cancelled"} and str(item["appointment_date"]) >= today]
        past = [item for item in appointments if str(item["appointment_date"]) < today and item["status"] not in {"cancelled"}]
        cancelled = [item for item in appointments if item["status"] == "cancelled"]
        rescheduled = [item for item in appointments if item["status"] == "rescheduled"]
        emergency = [item for item in appointments if str(item.get("urgency")) == "Emergency"]
        primary_appointment = upcoming[0] if upcoming else (appointments[0] if appointments else None)
        department = str(primary_appointment.get("specialty") if primary_appointment else "Not assigned yet")
        assigned_doctor = str(primary_appointment.get("doctor_name") if primary_appointment else "Choose a doctor")
        doctor_user = next((dict(row) for row in fetch_doctor_users(include_inactive=False) if str(row["doctor_name"] or row["name"]) == assigned_doctor), None)
        doctor_options = recommend_doctor_matches(department, phone=phone, patient_email=patient_email) if department != "Not assigned yet" else []
        for index, option in enumerate(doctor_options):
            option["relationship_label"] = "My Doctor" if option["doctor_name"] == assigned_doctor else ("Previously Visited" if option.get("previous_visits") else "Available Doctor")
            option["workload_label"] = "High availability" if int(option.get("open_slot_count") or 0) >= 6 else "Limited slots"
            option["patient_rating"] = "Coming soon"
            option["specialization"] = str(doctor_user.get("specialization") or option.get("department") or department) if doctor_user and option["doctor_name"] == assigned_doctor else str(option.get("department") or department)
        if primary_appointment:
            timeline.append(
                {
                    "date": str(primary_appointment.get("appointment_date") or ""),
                    "title": "Next Appointment",
                    "details": f"{primary_appointment.get('doctor_name')} · {primary_appointment.get('slot_time') or 'Time pending'}",
                    "status": "Upcoming",
                }
            )
        timeline = sorted(
            [item for item in timeline if item.get("date")],
            key=lambda item: str(item.get("date") or ""),
            reverse=True,
        )
        quick_summary = {
            "upcoming_appointment": upcoming[0] if upcoming else None,
            "last_consultation": past[0] if past else None,
            "active_prescription": next((item for item in prescriptions if str(item.get("status") or "").lower() in {"issued", "active"}), None),
            "uploaded_reports": len(reports),
            "pending_follow_up": next((item for item in appointments if str(item.get("follow_up_status") or "") not in {"", "none"}), None),
        }
        emergency_help = [
            "Chest pain",
            "Difficulty breathing",
            "Stroke symptoms",
            "Severe injury",
            "Heavy bleeding",
            "Broken bone",
            "Accident",
        ]
        quick_symptoms = [
            "Chest Pain",
            "Headache",
            "Fever",
            "Breathing Difficulty",
            "Skin Problem",
            "Joint Pain",
            "Stomach Pain",
            "Mental Health Concern",
        ]
        assessment_history = [
            {
                "date": str(item.get("created_at") or item.get("appointment_date") or "")[:10],
                "symptoms": str(item.get("symptoms") or "Health assessment"),
                "department": str(item.get("specialty") or "General Medicine"),
                "outcome": item.get("priority_label", "Medical guidance provided"),
                "doctor": str(item.get("doctor_name") or "Not assigned"),
                "appointment_status": str(item.get("care_status") or item.get("status") or "Recorded"),
            }
            for item in appointments[:8]
        ]
        last_assessment = assessment_history[0] if assessment_history else None
        return {
            "active_section": active_section,
            "sections": [{**item, "href": _patient_portal_url(item)} for item in patient_portal_sections],
            "workspace_context": workspace_context,
            "profile": profile,
            "patient_id": f"PAT-{int(profile['id']):05d}" if profile else f"USR-{int(g.user['id']):05d}",
            "department": department,
            "assigned_doctor": assigned_doctor,
            "doctor_profile": doctor_user,
            "current_status": _friendly_patient_status(primary_appointment),
            "records": records,
            "appointments": {
                "upcoming": upcoming,
                "past": past,
                "cancelled": cancelled,
                "rescheduled": rescheduled,
                "emergency": emergency,
                "all": appointments,
            },
            "quick_summary": quick_summary,
            "prescriptions": prescriptions,
            "reports": reports,
            "care_plans": care_plans,
            "monitoring": monitoring,
            "timeline": timeline,
            "doctor_options": doctor_options,
            "emergency_help": emergency_help,
            "quick_symptoms": quick_symptoms,
            "assessment_history": assessment_history,
            "last_assessment": last_assessment,
            "notifications": workspace_context.get("notifications", []),
        }

    @app.route("/patient/dashboard", methods=["GET"])
    @login_required
    @role_required("patient")
    def patient_dashboard():
        return redirect(url_for("intake"))

    @app.route("/patient/<section>", methods=["GET"])
    @login_required
    @role_required("patient")
    def patient_portal_section(section: str):
        return redirect(url_for("intake"))

    @app.route("/api/intake", methods=["POST"])
    def intake_api():
        payload = request.get_json(silent=True) or {}
        message = str(payload.get("message") or payload.get("symptoms") or request.form.get("user_input", "")).strip()
        vitals_payload = payload.get("vitals") if isinstance(payload.get("vitals"), dict) else {}
        profile = None
        if g.user and g.user.get("role") == "patient":
            profile = get_patient_profile(patient_email=g.user.get("email", ""))
        stored_age = int(profile["patient_age"]) if profile and profile["patient_age"] is not None else None
        stored_history = str(profile["chronic_conditions"] or "").strip() if profile else ""
        intake_state = session.get("docq_intake_state", {})
        engine = CaseWorkflowEngine()

        def _persist_intake_vitals(vitals: dict[str, object]) -> None:
            if not vitals:
                return
            record_patient_vitals(
                patient_name=str(profile["patient_name"] or "") if profile else "",
                patient_email=g.user.get("email", "") if g.user else "",
                phone=str(profile["phone"] or "") if profile else "",
                vitals=vitals,
            )

        if intake_state.get("awaiting_questionnaire"):
            questionnaire = dict(intake_state.get("questionnaire") or {})
            answers = dict(questionnaire.get("answers") or {})
            current_question_id = str(questionnaire.get("current_question_id") or "")
            if current_question_id:
                answers[current_question_id] = message
            questionnaire["answers"] = answers
            follow_up = next_question(questionnaire, answers)
            if follow_up:
                questionnaire["current_question_id"] = follow_up["id"]
                session["docq_intake_state"] = {
                    **intake_state,
                    "awaiting_questionnaire": True,
                    "questionnaire": questionnaire,
                }
                return jsonify(
                    {
                        "needs_more_info": True,
                        "follow_up_type": "clinical_questionnaire",
                        "follow_up_question": follow_up["text"],
                        "known_context": {
                            "used_age": intake_state.get("patient_age"),
                            "questionnaire": {
                                "id": questionnaire.get("id"),
                                "label": questionnaire.get("label"),
                                "answered": len(answers),
                                "total": len(questionnaire.get("questions") or []),
                            },
                        },
                        "workflow_trace": [{"agent": "questionnaire-agent", "decision": f"requested {follow_up['id']}"}],
                    }
                )
            session.pop("docq_intake_state", None)
            workflow_state = engine.run_intake(
                conversation_id=session.get("_csrf_token", "docq-session"),
                raw_message=str(intake_state.get("symptoms", "")).strip(),
                patient_id=str(profile["id"]) if profile and "id" in profile.keys() else None,
                patient_email=g.user.get("email", "") if g.user else "",
                patient_phone=str(profile["phone"] or "") if profile else "",
                actor_role=g.user.get("role", "public") if g.user else "public",
                profile=profile,
                stored_age=int(intake_state["patient_age"]) if intake_state.get("patient_age") not in (None, "") else stored_age,
                stored_history=stored_history,
                questionnaire_payload=questionnaire,
                vitals_payload=dict(intake_state.get("vitals") or {}),
            )
            _persist_intake_vitals(dict(intake_state.get("vitals") or {}))
            return jsonify(workflow_state.analysis)
        workflow_state = engine.run_intake(
            conversation_id=session.get("_csrf_token", "docq-session"),
            raw_message=message,
            patient_id=str(profile["id"]) if profile and "id" in profile.keys() else None,
            patient_email=g.user.get("email", "") if g.user else "",
            patient_phone=str(profile["phone"] or "") if profile else "",
            actor_role=g.user.get("role", "public") if g.user else "public",
            profile=profile,
            stored_age=stored_age,
            stored_history=stored_history,
            awaiting_age=bool(intake_state.get("awaiting_age")),
            prior_symptoms=str(intake_state.get("symptoms", "")).strip(),
            require_questionnaire=True,
            vitals_payload=vitals_payload or dict(intake_state.get("vitals") or {}),
        )
        if workflow_state.next_action == "collect_missing_info":
            session["docq_intake_state"] = {
                "awaiting_age": True,
                "symptoms": str(workflow_state.intake_data.get("symptoms", intake_state.get("symptoms", ""))).strip(),
                "vitals": vitals_payload or dict(intake_state.get("vitals") or {}),
            }
            return jsonify(
                {
                    "needs_more_info": True,
                    "follow_up_question": workflow_state.follow_up_questions[0],
                    "known_context": workflow_state.known_context,
                    "workflow_trace": workflow_state.workflow_trace,
                }
            )
        if workflow_state.next_action == "collect_clinical_questionnaire":
            questionnaire = dict(workflow_state.known_context.get("questionnaire") or {})
            session["docq_intake_state"] = {
                "awaiting_questionnaire": True,
                "symptoms": str(workflow_state.intake_data.get("symptoms", intake_state.get("symptoms", ""))).strip(),
                "patient_age": workflow_state.intake_data.get("patient_age"),
                "questionnaire": questionnaire,
                "vitals": vitals_payload or dict(intake_state.get("vitals") or {}),
            }
            return jsonify(
                {
                    "needs_more_info": True,
                    "follow_up_type": "clinical_questionnaire",
                    "follow_up_question": workflow_state.follow_up_questions[0],
                    "known_context": workflow_state.known_context,
                    "workflow_trace": workflow_state.workflow_trace,
                }
            )
        session.pop("docq_intake_state", None)
        _persist_intake_vitals(vitals_payload)
        return jsonify(workflow_state.analysis)

    @app.route("/api/public-booking", methods=["POST"])
    def public_booking():
        payload = request.get_json(silent=True) or {}
        try:
            appointment = create_appointment(payload, actor_name="Public Intake", actor_role="public", config=app.config)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        notification_rows = fetch_notifications_for_appointment(int(appointment["id"]), limit=12)
        notification_summary = [
            {
                "id": int(row["id"]),
                "channel": str(row["channel"]),
                "status": str(row["status"]),
                "correlation_id": str(row["correlation_id"] or ""),
                "message_category": str(row["message_category"] or ""),
                "external_id": str(row["external_id"] or ""),
            }
            for row in notification_rows
        ]
        return jsonify(
            {
                "message": (
                    f"Appointment request received for {appointment['appointment_date']} at {appointment['slot_time']} with "
                    f"{appointment['doctor_name']}."
                ),
                "appointment": appointment,
                "notification_summary": notification_summary,
                "notification_count": len(notification_summary),
                "whatsapp_onboarding": _build_whatsapp_sandbox_onboarding(),
            }
        ), 201

    @app.route("/api/doctor-availability", methods=["GET"])
    def doctor_availability():
        doctor_name = str(request.args.get("doctor_name", "")).strip()
        preferred_date = str(request.args.get("preferred_date", "")).strip()
        start_date = str(request.args.get("start_date", "")).strip()
        specialty = str(request.args.get("specialty", "")).strip()
        if not doctor_name:
            return jsonify({"error": "doctor_name is required."}), 400
        calendar = build_doctor_calendar(
            doctor_name,
            start_date=start_date or preferred_date or None,
            days=42,
            preferred_date=preferred_date,
            specialty=specialty,
        )
        workload = doctor_workload(doctor_name)
        return jsonify(
            {
                "doctor_name": doctor_name,
                "available_dates": compact_available_dates(calendar),
                "calendar": calendar,
                "workload": workload,
                "recommendation": calendar.get("first_available"),
            }
        )

    @app.route("/api/doctor-options", methods=["GET"])
    def doctor_options():
        symptoms = str(request.args.get("symptoms", "")).strip()
        requested_specialty = str(request.args.get("specialty", "")).strip()
        patient_email = str(request.args.get("patient_email", "")).strip()
        phone = str(request.args.get("phone", "")).strip()
        preferred_date = str(request.args.get("preferred_date", "")).strip()
        raw_age = str(request.args.get("patient_age", "")).strip()
        try:
            patient_age = int(raw_age) if raw_age else None
        except ValueError:
            patient_age = None
        classification = classify_department(symptoms, fallback_specialty=requested_specialty or "General", patient_age=patient_age)
        specialty = str(classification["specialty"])
        matches = recommend_doctor_matches(specialty, phone=phone, patient_email=patient_email)
        department_calendar = build_department_calendar(
            specialty,
            doctors=[str(match["doctor_name"]) for match in matches],
            start_date=preferred_date or None,
            days=42,
            preferred_date=preferred_date,
        )
        best_match = matches[0] if matches else {}
        return jsonify(
            {
                "classification": classification,
                "specialty": specialty,
                "department": classification["department"],
                "doctor_matches": matches,
                "department_calendar": department_calendar,
                "recommended_appointment": {
                    "doctor_name": best_match.get("doctor_name", ""),
                    "slot": best_match.get("next_available_slot", ""),
                    "reason": "Earliest available specialist with the best availability and workload score.",
                    "availability_score": best_match.get("availability_score", 0),
                },
                "selection_policy": "patient_choice_or_earliest_available",
                "message": f"Select a doctor in {classification['department']} or continue with the earliest available appointment.",
            }
        )

    @app.route("/api/cron/reminders", methods=["GET"])
    def cron_reminders():
        if not _is_authorized_cron_request():
            return jsonify({"error": "unauthorized"}), 401
        reminders_queued = send_due_reminders(app.config)
        notifications_processed = process_notification_queue(app.config)
        record_automation_run(
            "vercel-cron-reminders",
            "completed",
            f"Queued {reminders_queued} reminder batch(es) and processed {notifications_processed} notification job(s).",
            int(reminders_queued) + int(notifications_processed),
        )
        return jsonify(
            {
                "status": "ok",
                "reminders_queued": reminders_queued,
                "notifications_processed": notifications_processed,
            }
        )

    @app.route("/api/reports/upload", methods=["POST"])
    @login_required
    def report_upload_api():
        try:
            appointment_id = int(request.form.get("appointment_id") or (request.get_json(silent=True) or {}).get("appointment_id", 0))
            result = _process_report_upload(appointment_id)
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"message": "Report uploaded and analyzed.", "report": result}), 201

    @app.route("/api/automation/reminders", methods=["GET"])
    def automation_reminders():
        target_date = request.args.get("target_date") or (dt.date.today() + dt.timedelta(days=1)).isoformat()
        try:
            dt.datetime.strptime(target_date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "target_date must be in YYYY-MM-DD format."}), 400
        items = []
        for row in fetch_due_reminders(target_date):
            items.append(
                {
                    "appointment_id": row["appointment_id"],
                    "patient_name": row["patient_name"],
                    "patient_email": row["patient_email"],
                    "phone": row["phone"],
                    "doctor_name": row["doctor_name"],
                    "appointment_date": row["appointment_date"],
                    "slot_time": row["slot_time"],
                    "channel": "sms",
                }
            )
        return jsonify({"target_date": target_date, "items": items})

    @app.route("/api/automation/reminders/mark-sent", methods=["POST"])
    def automation_mark_reminder_sent():
        payload = request.get_json(silent=True) or {}
        appointment_id = payload.get("appointment_id")
        status = str(payload.get("status", "")).strip().lower()
        if not appointment_id:
            return jsonify({"error": "appointment_id is required."}), 400
        if status not in {"sent", "failed", "retry"}:
            return jsonify({"error": "status must be one of sent, failed, retry."}), 400
        try:
            appointment_id = int(appointment_id)
        except (TypeError, ValueError):
            return jsonify({"error": "appointment_id must be an integer."}), 400
        mark_reminder_delivery(appointment_id, status)
        record_automation_run("n8n-reminder-update", "completed", f"Reminder delivery updated to {status} for appointment {appointment_id}.", 1)
        return jsonify({"status": "ok", "appointment_id": appointment_id, "delivery_status": status})

    @app.route("/api/workflows/<workflow_id>/events", methods=["GET"])
    def workflow_events_api(workflow_id: str):
        try:
            limit = int(request.args.get("limit", "40"))
        except ValueError:
            return jsonify({"error": "limit must be an integer."}), 400
        events = [model_dump(build_workflow_event_record(row)) for row in fetch_workflow_events(workflow_id, limit=max(1, min(limit, 200)))]
        return jsonify({"version": EVENT_SCHEMA_VERSION, "workflow_id": workflow_id, "events": events})

    @app.route("/api/workflows/<workflow_id>/replay", methods=["GET"])
    def workflow_replay_api(workflow_id: str):
        try:
            limit = int(request.args.get("limit", "40"))
        except ValueError:
            return jsonify({"error": "limit must be an integer."}), 400
        replay = build_workflow_replay(workflow_id, limit=max(1, min(limit, 200)))
        return jsonify(replay)

    @app.route("/api/workflows/diff", methods=["GET"])
    def workflow_diff_api():
        workflow_a = str(request.args.get("workflow_a", "")).strip()
        workflow_b = str(request.args.get("workflow_b", "")).strip()
        if not workflow_a or not workflow_b:
            return jsonify({"error": "workflow_a and workflow_b are required."}), 400
        try:
            limit = int(request.args.get("limit", "60"))
        except ValueError:
            return jsonify({"error": "limit must be an integer."}), 400
        diff = build_workflow_replay_diff(workflow_a, workflow_b, limit=max(1, min(limit, 200)))
        return jsonify(diff)

    @app.route("/api/workflows/<workflow_id>/integrity", methods=["GET"])
    def workflow_integrity_api(workflow_id: str):
        metrics = build_dashboard_metrics(app.config, workflow_id=workflow_id)
        integrity = metrics.get("replay_integrity")
        if not integrity:
            return jsonify({"error": "workflow replay not found."}), 404
        return jsonify(integrity)

    @app.route("/api/workflows/summary", methods=["GET"])
    def workflow_summary_api():
        metrics = WorkflowMetricsSummary(**build_dashboard_metrics(app.config).get("workflow_metrics", {}))
        payload = model_dump(metrics)
        return jsonify(payload)

    @app.route("/api/workflows/active", methods=["GET"])
    def workflow_active_api():
        metrics = build_dashboard_metrics(app.config).get("workflow_metrics", {})
        return jsonify({"version": EVENT_SCHEMA_VERSION, "active_workflows": metrics.get("activity_feed", [])})

    @app.route("/api/workflows/metrics", methods=["GET"])
    def workflow_metrics_api():
        metrics = WorkflowMetricsSummary(**build_dashboard_metrics(app.config).get("workflow_metrics", {}))
        return jsonify(
            {
                "version": metrics.version,
                "active_workflows": metrics.active_workflows,
                "human_review_queue": metrics.human_review_queue,
                "emergency_escalations": metrics.emergency_escalations,
                "autonomous_bookings": metrics.autonomous_bookings,
                "failed_recoveries": metrics.failed_recoveries,
                "average_confidence": metrics.average_confidence,
                "decision_breakdown": metrics.decision_breakdown,
            }
        )

    @app.route("/api/workflows/intelligence", methods=["GET"])
    def workflow_intelligence_api():
        intelligence = build_dashboard_metrics(app.config).get("operational_intelligence", {})
        return jsonify(intelligence)

    @app.route("/api/workflows/anomalies", methods=["GET"])
    def workflow_anomalies_api():
        intelligence = build_dashboard_metrics(app.config).get("operational_intelligence", {})
        return jsonify({"version": EVENT_SCHEMA_VERSION, "anomalies": intelligence.get("anomalies", [])})

    @app.route("/api/workflows/model-diff", methods=["GET"])
    def workflow_model_diff_api():
        workflow_id = str(request.args.get("workflow_id", "")).strip()
        if not workflow_id:
            return jsonify({"error": "workflow_id is required."}), 400
        payload = build_workflow_model_diff(workflow_id)
        if not payload:
            return jsonify({"error": "model diff not found for workflow."}), 404
        return jsonify(payload)

    @app.route("/api/workflows/drift", methods=["GET"])
    def workflow_drift_api():
        return jsonify(build_drift_detection_summary())

    @app.route("/api/ml/evaluations", methods=["GET"])
    @role_required("admin", "governance_analyst", "auditor")
    def model_evaluations_api():
        refresh = str(request.args.get("refresh", "")).strip().lower() in {"1", "true", "yes"}
        if refresh or not list_model_evaluations(limit=1):
            run_offline_model_evaluation(str(request.args.get("scope", "latest-25")).strip() or "latest-25")
        return jsonify({"version": EVENT_SCHEMA_VERSION, "runs": list_model_evaluations(limit=20)})

    @app.route("/api/ml/evaluations/<int:run_id>", methods=["GET"])
    @role_required("admin", "governance_analyst", "auditor")
    def model_evaluation_run_api(run_id: int):
        try:
            run = get_model_evaluation_run(run_id)
        except LookupError:
            return jsonify({"error": "evaluation run not found."}), 404
        run["results"] = get_model_evaluation_results(run_id)
        return jsonify(run)

    @app.route("/api/ml/evaluations/<int:run_id>/diff", methods=["GET"])
    @role_required("admin", "governance_analyst", "auditor")
    def model_evaluation_diff_api(run_id: int):
        return jsonify(get_model_evaluation_diff(run_id))

    @app.route("/api/ml/evaluations/<int:run_id>/drift", methods=["GET"])
    @role_required("admin", "governance_analyst", "auditor")
    def model_evaluation_drift_api(run_id: int):
        payload = get_model_evaluation_drift(run_id)
        if payload is None:
            return jsonify({"error": "evaluation drift snapshot not found."}), 404
        return jsonify(payload)

    @app.route("/api/ml/evaluations/<int:run_id>/promotion-gate", methods=["GET"])
    @role_required("admin", "governance_analyst", "auditor")
    def model_evaluation_gate_api(run_id: int):
        payload = get_model_evaluation_promotion_gate(run_id)
        if not payload:
            return jsonify({"error": "promotion gate result not found."}), 404
        return jsonify(payload)

    @app.route("/api/ml/governance/recommendations", methods=["GET"])
    @role_required("admin", "governance_analyst", "auditor")
    def governance_recommendations_api():
        refresh = str(request.args.get("refresh", "")).strip().lower() in {"1", "true", "yes"}
        if refresh:
            run_continuous_governance(refresh=True)
        items = [model_dump(build_governance_recommendation_contract(row)) for row in fetch_governance_recommendations(limit=50)]
        return jsonify({"version": EVENT_SCHEMA_VERSION, "recommendations": items})

    @app.route("/api/ml/governance/recommendations/<int:recommendation_id>", methods=["GET"])
    @role_required("admin", "governance_analyst", "auditor")
    def governance_recommendation_api(recommendation_id: int):
        row = fetch_governance_recommendation(recommendation_id)
        if row is None:
            return jsonify({"error": "governance recommendation not found."}), 404
        return jsonify(model_dump(build_governance_recommendation_contract(row)))

    @app.route("/api/ml/governance/rollouts", methods=["GET"])
    @role_required("admin", "governance_analyst", "auditor")
    def governance_rollouts_api():
        items = [model_dump(build_rollout_profile_contract(row)) for row in fetch_rollout_profiles(limit=20)]
        return jsonify({"version": EVENT_SCHEMA_VERSION, "rollouts": items})

    @app.route("/api/ml/governance/timeline", methods=["GET"])
    @role_required("admin", "governance_analyst", "auditor")
    def governance_timeline_api():
        items = [model_dump(build_governance_timeline_event_contract(row)) for row in fetch_governance_timeline(limit=100)]
        return jsonify({"version": EVENT_SCHEMA_VERSION, "timeline": items})

    @app.route("/api/ml/governance/drift-triggers", methods=["GET"])
    @role_required("admin", "governance_analyst", "auditor")
    def governance_drift_triggers_api():
        state = run_continuous_governance(refresh=False)
        return jsonify({"version": EVENT_SCHEMA_VERSION, "drift_triggers": state.get("drift_triggers", [])})

    @app.route("/api/ml/governance/state", methods=["GET"])
    @role_required("admin", "governance_analyst", "auditor")
    def governance_state_api():
        refresh = str(request.args.get("refresh", "")).strip().lower() in {"1", "true", "yes"}
        return jsonify(run_continuous_governance(refresh=refresh))

    def _tenant_from_request() -> str:
        tenant_key = str(request.args.get("tenant_key", "")).strip() or (g.user.get("tenant_key") if g.user else "") or app.config.get("DEFAULT_TENANT_KEY", "default-clinic")
        if not user_has_tenant_access(g.user, tenant_key):
            raise PermissionError(f"no access to tenant {tenant_key}")
        return tenant_key

    def _serialize_workspace_context(context: dict[str, object] | None) -> dict[str, object]:
        if not context:
            return {"profile": None, "recent_visits": [], "upcoming_appointments": [], "timeline": [], "communication_preferences": {}, "notifications": []}
        return {
            "profile": dict(context.get("profile")) if context.get("profile") is not None else None,
            "recent_visits": [dict(item) for item in context.get("recent_visits", [])],
            "upcoming_appointments": [dict(item) for item in context.get("upcoming_appointments", [])],
            "timeline": list(context.get("timeline", [])),
            "communication_preferences": dict(context.get("communication_preferences", {})),
            "notifications": [dict(item) for item in context.get("notifications", [])],
        }

    @app.route("/api/analytics/operational", methods=["GET"])
    @role_required("admin", "operations", "operations_manager", "clinic_admin", "hospital_admin", "auditor")
    def operational_analytics_api():
        tenant_key = _tenant_from_request()
        return jsonify(build_operational_analytics(tenant_key=tenant_key))

    @app.route("/api/compliance/audit-export", methods=["GET"])
    @role_required("admin", "compliance_officer", "auditor", "hospital_admin")
    def compliance_audit_export_api():
        tenant_key = _tenant_from_request()
        log_sensitive_access(
            tenant_key=tenant_key,
            access_type="audit_export",
            resource_type="tenant",
            resource_id=tenant_key,
            masked_fields={"tenant_key": mask_sensitive_value(tenant_key)},
        )
        return jsonify(export_audit_bundle(tenant_key))

    @app.route("/api/disaster-recovery/export", methods=["POST"])
    @role_required("admin", "hospital_admin", "compliance_officer")
    def disaster_recovery_export_api():
        tenant_key = _tenant_from_request()
        return jsonify(export_recovery_bundle(tenant_key=tenant_key))

    @app.route("/api/disaster-recovery/export/<int:export_id>/verify", methods=["GET"])
    @role_required("admin", "hospital_admin", "compliance_officer", "auditor")
    def disaster_recovery_verify_api(export_id: int):
        return jsonify(verify_recovery_bundle(export_id))

    @app.route("/api/tenants/<tenant_key>/state", methods=["GET"])
    @role_required("admin", "clinic_admin", "hospital_admin", "auditor", "compliance_officer")
    def tenant_state_api(tenant_key: str):
        if not user_has_tenant_access(g.user, tenant_key):
            return jsonify({"error": "forbidden"}), 403
        return jsonify(fetch_tenant_summary(tenant_key))

    @app.route("/api/mobile/appointments", methods=["GET"])
    @login_required
    def mobile_appointments_api():
        tenant_key = _tenant_from_request()
        items = [item for item in fetch_appointments(limit=50) if str(item["tenant_key"] or app.config.get("DEFAULT_TENANT_KEY")) == tenant_key]
        log_sensitive_access(
            tenant_key=tenant_key,
            access_type="mobile_appointments_read",
            resource_type="appointments",
            resource_id=tenant_key,
            masked_fields={"count": str(len(items))},
        )
        return jsonify({"tenant_key": tenant_key, "appointments": items})

    @app.route("/api/mobile/check-in", methods=["POST"])
    @login_required
    def mobile_checkin_api():
        payload = request.get_json(silent=True) or {}
        appointment_id = int(payload.get("appointment_id", 0))
        update_appointment_status(appointment_id, queue_state="checked-in", status="checked-in")
        return jsonify({"status": "ok", "appointment_id": appointment_id})

    @app.route("/api/mobile/reminder-ack", methods=["POST"])
    @login_required
    def mobile_reminder_ack_api():
        payload = request.get_json(silent=True) or {}
        appointment_id = int(payload.get("appointment_id", 0))
        channel = str(payload.get("channel", "sms")).strip().lower() or "sms"
        from .reminder_runtime import record_reminder_outcome

        record_reminder_outcome(appointment_id, reminder_type="appointment_reminder", delivery_status="sent", attempts=1)
        record_security_event(
            f"reminder-ack-{appointment_id}-{channel}",
            action="reminder_acknowledged",
            decision="accepted",
            payload={"appointment_id": appointment_id, "channel": channel, "user_id": g.user["id"]},
            confidence=100.0,
        )
        return jsonify({"status": "acknowledged", "appointment_id": appointment_id, "channel": channel})

    @app.route("/api/provider/appointments/<int:appointment_id>/approve", methods=["POST"])
    @role_required("doctor", "clinician", "department_supervisor")
    def provider_appointment_approve_api(appointment_id: int):
        update_appointment_status(appointment_id, queue_state="scheduled", status="doctor-acknowledged", acknowledged_by=g.user["name"])
        return jsonify({"status": "approved", "appointment_id": appointment_id})

    @app.route("/api/provider/coordination/<int:queue_item_id>/assign", methods=["POST"])
    @role_required("operations", "operations_manager", "clinic_admin", "hospital_admin", "department_supervisor")
    def provider_coordination_assign_api(queue_item_id: int):
        payload = request.get_json(silent=True) or {}
        owner = str(payload.get("owner", g.user["name"])).strip() or g.user["name"]
        item = assign_queue_item(queue_item_id, owner=owner, worker_id=f"assign:{g.user['id']}")
        return jsonify(model_dump(item))

    @app.route("/api/billing/events", methods=["POST"])
    @role_required("admin", "operations_manager", "clinic_admin", "hospital_admin")
    def billing_event_api():
        payload = request.get_json(silent=True) or {}
        event = record_billing_event(
            appointment_id=int(payload.get("appointment_id", 0)),
            workflow_id=str(payload.get("workflow_id", "")),
            event_type=str(payload.get("event_type", "billing_pending")),
            amount_cents=int(payload.get("amount_cents", 0)),
            status=str(payload.get("status", "pending")),
            payload={"tenant_key": _tenant_from_request(), **payload},
        )
        return jsonify(event), 201

    @app.route("/api/integrations/health", methods=["GET"])
    @role_required("admin", "operations_manager", "clinic_admin", "hospital_admin", "auditor")
    def integrations_health_api():
        tenant_key = _tenant_from_request()
        activate = str(request.args.get("activate", "")).strip().lower() in {"1", "true", "yes"}
        return jsonify(
            {
                "tenant_key": tenant_key,
                "providers": [
                    twilio_sms_adapter(
                        tenant_key=tenant_key,
                        target="+10000000000",
                        message="health",
                        account_sid=app.config.get("TWILIO_ACCOUNT_SID"),
                        from_number=app.config.get("TWILIO_FROM_NUMBER"),
                    ),
                    twilio_whatsapp_adapter(
                        tenant_key=tenant_key,
                        target="+10000000000",
                        message="health",
                        account_sid=app.config.get("TWILIO_ACCOUNT_SID"),
                        from_number=app.config.get("TWILIO_WHATSAPP_FROM"),
                    ),
                    sendgrid_email_adapter(
                        tenant_key=tenant_key,
                        target="health@example.com",
                        subject="health",
                        body="ok",
                        smtp_host=app.config.get("SMTP_HOST"),
                        smtp_port=int(app.config.get("SMTP_PORT", 465)),
                        smtp_username=app.config.get("SMTP_USERNAME"),
                        smtp_password=app.config.get("SMTP_PASSWORD"),
                        smtp_from=app.config.get("SMTP_FROM"),
                        activate=activate and bool(app.config.get("ENABLE_EXTERNAL_INTEGRATIONS")),
                    ),
                    google_calendar_adapter(
                        tenant_key=tenant_key,
                        appointment_ref="health-check",
                        client_id=app.config.get("GOOGLE_CALENDAR_CLIENT_ID"),
                        refresh_token=app.config.get("GOOGLE_CALENDAR_REFRESH_TOKEN"),
                    ),
                    outlook_calendar_adapter(
                        tenant_key=tenant_key,
                        appointment_ref="health-check",
                        client_id=app.config.get("OUTLOOK_CALENDAR_CLIENT_ID"),
                        tenant_id=app.config.get("OUTLOOK_CALENDAR_TENANT_ID"),
                        refresh_token=app.config.get("OUTLOOK_CALENDAR_REFRESH_TOKEN"),
                    ),
                    slack_webhook_adapter(
                        tenant_key=tenant_key,
                        message="health",
                        webhook_url=app.config.get("SLACK_WEBHOOK_URL"),
                        activate=activate and bool(app.config.get("ENABLE_EXTERNAL_INTEGRATIONS")),
                    ),
                    webhook_delivery_adapter(
                        tenant_key=tenant_key,
                        webhook_url=app.config.get("GENERIC_WEBHOOK_URL"),
                        payload={"probe": "integration-health", "tenant_key": tenant_key},
                        activate=activate and bool(app.config.get("ENABLE_EXTERNAL_INTEGRATIONS")),
                    ),
                ],
            }
        )

    @app.route("/api/observability/topology", methods=["GET"])
    @role_required("admin", "auditor", "operations", "operations_manager", "hospital_admin", "clinic_admin")
    def observability_topology_api():
        return jsonify({"nodes": list_runtime_nodes(), "request_id": getattr(g, "request_id", "")})

    @app.route("/api/observability/dashboards", methods=["GET"])
    @role_required("admin", "auditor", "operations", "operations_manager", "hospital_admin", "clinic_admin")
    def observability_dashboards_api():
        tenant_key = _tenant_from_request()
        analytics = build_operational_analytics(tenant_key=tenant_key)
        return jsonify(
            {
                "tenant_key": tenant_key,
                "dashboards": [
                    {"key": "runtime-health", "title": "Distributed runtime health", "summary": f"{len(list_runtime_nodes())} runtime nodes active"},
                    {"key": "replay-throughput", "title": "Replay throughput", "summary": f"Replay latency {analytics['replay_latency_ms']}ms"},
                    {"key": "queue-pressure", "title": "Queue pressure", "summary": f"{sum(analytics['queue_throughput'].values())} queue-state records"},
                    {"key": "governance-backlog", "title": "Governance backlog", "summary": f"{analytics['governance_review_count']} review items"},
                ],
            }
        )

    @app.route("/api/deployment/validate", methods=["GET"])
    @role_required("admin", "hospital_admin", "clinic_admin", "auditor")
    def deployment_validate_api():
        return jsonify(build_deployment_health_panel(app.config, Path(app.config["BASE_DIR"])))

    @app.route("/api/demo/bootstrap", methods=["POST"])
    @role_required("admin", "hospital_admin", "clinic_admin")
    def demo_bootstrap_api():
        return jsonify(bootstrap_demo_environment(app.config))

    @app.route("/api/load-tests/benchmarks", methods=["POST"])
    @role_required("admin", "hospital_admin", "clinic_admin", "auditor")
    def load_benchmarks_api():
        payload = request.get_json(silent=True) or {}
        iterations = max(1, min(int(payload.get("iterations", 2)), 5))
        return jsonify(run_benchmark_suite(app.config, iterations=iterations))

    @app.route("/api/chaos/run", methods=["POST"])
    @role_required("admin", "hospital_admin", "clinic_admin", "auditor")
    def chaos_run_api():
        payload = request.get_json(silent=True) or {}
        tenant_key = _tenant_from_request()
        scenario = str(payload.get("scenario", "worker-crash")).strip() or "worker-crash"
        experiment_key = str(payload.get("experiment_key", f"{scenario}-{int(time.time())}"))
        return jsonify(run_chaos_experiment(experiment_key=experiment_key, scenario=scenario, tenant_key=tenant_key, actor_name=g.user["name"]))

    @app.route("/health", methods=["GET"])
    def health_api():
        return jsonify({"status": "ok", "service": "docq", "env": app.config.get("ENV_NAME", "development")})

    @app.route("/ready", methods=["GET"])
    def ready_api():
        database_ready = check_database_readiness(app.config["DATABASE_URL"])
        status_code = 200 if database_ready else 503
        return jsonify({"status": "ready" if database_ready else "degraded", "database": database_ready}), status_code

    @app.route("/metrics", methods=["GET"])
    def metrics_api():
        return Response(metrics_registry.render_prometheus(), mimetype="text/plain; version=0.0.4")

    @app.route("/api/workflows/stream", methods=["GET"])
    def workflow_stream_api():
        workflow_id = str(request.args.get("workflow_id", "")).strip()
        compare_workflow_id = str(request.args.get("compare_workflow_id", "")).strip()

        def generate():
            last_payload = ""
            for _ in range(10):
                snapshot = build_workflow_console_snapshot(app.config, workflow_id=workflow_id, compare_workflow_id=compare_workflow_id)
                payload = json.dumps(snapshot)
                if payload != last_payload:
                    yield f"event: workflow\ndata: {payload}\n\n"
                    last_payload = payload
                time.sleep(2)

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.route("/schedule", methods=["GET", "POST"])
    @login_required
    def schedule():
        if g.user["role"] == "patient":
            return redirect(url_for("intake"))
        error = None
        success = None
        if request.method == "POST":
            try:
                action = request.form.get("action", "create")
                if action == "create":
                    payload = {
                        "patient_name": request.form.get("patient_name", ""),
                        "patient_email": request.form.get("patient_email", ""),
                        "phone": request.form.get("phone", ""),
                        "patient_age": request.form.get("patient_age", ""),
                        "symptoms": request.form.get("symptoms", ""),
                        "specialty": request.form.get("specialty", ""),
                        "appointment_date": request.form.get("appointment_date", ""),
                    }
                    appointment = create_appointment(payload, actor_name=g.user["name"], actor_role=g.user["role"], config=app.config)
                    success = f"Appointment confirmed for {appointment['patient_name']} with {appointment['doctor_name']} on {appointment['appointment_date']} at {appointment['slot_time']}."
                elif action == "reschedule":
                    reschedule_appointment(int(request.form.get("appointment_id", "0")), request.form.get("new_appointment_date", ""), g.user["name"], g.user["role"])
                    success = "Appointment rescheduled successfully."
                elif action == "reassign":
                    reassign_appointment_doctor(
                        int(request.form.get("appointment_id", "0")),
                        request.form.get("doctor_name", ""),
                        request.form.get("governance_reason", ""),
                        g.user["name"],
                        g.user["role"],
                    )
                    success = "Doctor reassigned and appointment governance log updated."
                elif action == "escalate":
                    escalate_appointment_priority(
                        int(request.form.get("appointment_id", "0")),
                        request.form.get("governance_reason", ""),
                        g.user["name"],
                        g.user["role"],
                    )
                    success = "Appointment priority escalated for doctor review."
                elif action == "cancel":
                    cancel_appointment(int(request.form.get("appointment_id", "0")), request.form.get("cancel_reason", ""), g.user["name"], g.user["role"])
                    success = "Appointment cancelled and slot released."
            except ValueError as exc:
                error = str(exc)
        metrics = build_dashboard_metrics(
            app.config,
            workflow_id=str(request.args.get("workflow_id", "")).strip(),
            compare_workflow_id=str(request.args.get("compare_workflow_id", "")).strip(),
        )
        return render_template("schedule.html", metrics=metrics, appointments=metrics["recent_appointments"], error=error, success=success, specialities=sorted(SPECIALTY_LABELS.keys()))

    @app.route("/api/appointments", methods=["POST"])
    @login_required
    def appointment_api():
        if g.user["role"] == "patient":
            return jsonify({"error": "Patients should use the intake and booking flow from the home page."}), 403
        try:
            appointment = create_appointment(request.get_json(silent=True) or {}, actor_name=g.user["name"], actor_role=g.user["role"], config=app.config)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"message": "Appointment booked successfully.", "appointment": appointment}), 201

    @app.route("/dashboard")
    @login_required
    def dashboard():
        if g.user["role"] == "patient":
            return redirect(url_for("intake"))
        if g.user["role"] in {"doctor", "clinician"}:
            return redirect(url_for("doctor_inbox"))
        metrics = build_dashboard_metrics(
            app.config,
            workflow_id=str(request.args.get("workflow_id", "")).strip(),
            compare_workflow_id=str(request.args.get("compare_workflow_id", "")).strip(),
        )
        return render_template("schedule.html", metrics=metrics, appointments=metrics["recent_appointments"], error=None, success=None, specialities=sorted(SPECIALTY_LABELS.keys()))

    @app.route("/admin")
    @role_required(*admin_ops_roles)
    def admin_dashboard():
        metrics = build_dashboard_metrics(
            app.config,
            workflow_id=str(request.args.get("workflow_id", "")).strip(),
            compare_workflow_id=str(request.args.get("compare_workflow_id", "")).strip(),
        )
        return render_template("schedule.html", metrics=metrics, appointments=metrics["recent_appointments"], error=None, success=None, specialities=sorted(SPECIALTY_LABELS.keys()), admin_mode=True)

    @app.route("/admin/doctors", methods=["GET"])
    @role_required(*admin_ops_roles)
    def admin_doctors_api():
        include_inactive = str(request.args.get("include_inactive", "")).lower() in {"1", "true", "yes"}
        return jsonify(
            {
                "items": [dict(row) for row in fetch_doctor_users(include_inactive=include_inactive)],
                "request_id": getattr(g, "request_id", ""),
            }
        )

    @app.route("/admin/doctors", methods=["POST"])
    @role_required(*admin_ops_roles)
    def admin_create_doctor_api():
        payload = request.get_json(silent=True) or request.form
        name = str(payload.get("name", "")).strip()
        email = str(payload.get("email", "")).strip().lower()
        password = str(payload.get("password", "")).strip()
        specialty = normalize_specialty(str(payload.get("department") or payload.get("specialty") or "General"))
        doctor_name = str(payload.get("doctor_name") or name).strip()
        branch = str(payload.get("branch") or app.config.get("DEFAULT_BRANCH", "Mysore Central")).strip()
        specialization = str(payload.get("specialization", "")).strip()
        availability = str(payload.get("availability") or "Available").strip()
        status = str(payload.get("status") or "active").strip()
        phone = str(payload.get("phone", "")).strip() or None
        role = str(payload.get("role") or "doctor").strip()
        if role not in {"doctor", "clinician"}:
            return jsonify({"error": "doctor role must be doctor or clinician."}), 400
        if not name or not email or len(password) < 8:
            return jsonify({"error": "name, email, and password of at least 8 characters are required."}), 400
        if get_user_by_email(email):
            return jsonify({"error": "doctor account already exists."}), 409
        user_id = create_user(
            name=name,
            email=email,
            password=password,
            role=role,
            branch=branch,
            tenant_key=str(g.user.get("tenant_key") or app.config.get("DEFAULT_TENANT_KEY", "default-clinic")),
            org_unit=specialty,
            phone=phone,
            doctor_name=doctor_name,
            specialty=specialty,
            specialization=specialization,
            status=status,
            availability=availability,
            email_verified=True,
        )
        seed_slots_for_doctor(doctor_name, specialty, branch)
        log_action(g.user["name"], g.user["role"], "create-doctor", "doctor", user_id, f"{doctor_name} assigned to {specialty}")
        return jsonify({"message": "Doctor created.", "doctor_id": user_id, "request_id": getattr(g, "request_id", "")}), 201

    @app.route("/admin/doctors/<int:user_id>", methods=["PATCH"])
    @role_required(*admin_ops_roles)
    def admin_update_doctor_api(user_id: int):
        payload = request.get_json(silent=True) or request.form
        department = payload.get("department") or payload.get("specialty")
        update_doctor_user(
            user_id,
            name=str(payload.get("name")).strip() if payload.get("name") is not None else None,
            department=str(department).strip() if department is not None else None,
            branch=str(payload.get("branch")).strip() if payload.get("branch") is not None else None,
            specialization=str(payload.get("specialization")).strip() if payload.get("specialization") is not None else None,
            status=str(payload.get("status")).strip() if payload.get("status") is not None else None,
            availability=str(payload.get("availability")).strip() if payload.get("availability") is not None else None,
        )
        log_action(g.user["name"], g.user["role"], "update-doctor", "doctor", user_id, "doctor management profile updated")
        return jsonify({"message": "Doctor updated.", "request_id": getattr(g, "request_id", "")})

    @app.route("/admin/doctors/<int:user_id>", methods=["DELETE"])
    @role_required(*admin_ops_roles)
    def admin_deactivate_doctor_api(user_id: int):
        update_doctor_user(user_id, status="inactive", availability="Offline")
        log_action(g.user["name"], g.user["role"], "deactivate-doctor", "doctor", user_id, "doctor account deactivated")
        return jsonify({"message": "Doctor deactivated.", "request_id": getattr(g, "request_id", "")})

    @app.route("/admin/appointments/<int:appointment_id>/reschedule", methods=["POST"])
    @role_required(*admin_ops_roles)
    def admin_reschedule_appointment_api(appointment_id: int):
        payload = request.get_json(silent=True) or request.form
        try:
            reschedule_appointment(appointment_id, str(payload.get("new_appointment_date", "")).strip(), g.user["name"], g.user["role"])
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"message": "Appointment rescheduled.", "appointment": dict(get_appointment(appointment_id))})

    @app.route("/admin/appointments/<int:appointment_id>/reassign", methods=["POST"])
    @role_required(*admin_ops_roles)
    def admin_reassign_appointment_api(appointment_id: int):
        payload = request.get_json(silent=True) or request.form
        try:
            reassign_appointment_doctor(
                appointment_id,
                str(payload.get("doctor_name", "")).strip(),
                str(payload.get("reason", "") or payload.get("governance_reason", "")).strip(),
                g.user["name"],
                g.user["role"],
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"message": "Doctor reassigned.", "appointment": dict(get_appointment(appointment_id))})

    @app.route("/admin/appointments/<int:appointment_id>/escalate", methods=["POST"])
    @role_required(*admin_ops_roles)
    def admin_escalate_appointment_api(appointment_id: int):
        payload = request.get_json(silent=True) or request.form
        try:
            escalate_appointment_priority(
                appointment_id,
                str(payload.get("reason", "") or payload.get("governance_reason", "")).strip(),
                g.user["name"],
                g.user["role"],
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"message": "Appointment priority escalated.", "appointment": dict(get_appointment(appointment_id))})

    @app.route("/admin/replay/<workflow_id>", methods=["GET"])
    @role_required(*admin_ops_roles)
    def admin_replay_api(workflow_id: str):
        limit = max(1, min(int(request.args.get("limit", 80)), 200))
        replay = build_workflow_replay(workflow_id, limit=limit)
        events = [model_dump(build_workflow_event_record(row)) for row in fetch_workflow_events(workflow_id, limit=limit)]
        return jsonify({"workflow_id": workflow_id, "replay": replay, "events": events, "request_id": getattr(g, "request_id", "")})

    @app.route("/admin/events", methods=["GET"])
    @role_required(*admin_ops_roles)
    def admin_events_api():
        page = max(1, int(request.args.get("page", 1)))
        page_size = max(1, min(int(request.args.get("page_size", 25)), 100))
        workflow_id = str(request.args.get("workflow_id", "")).strip()
        event_type = str(request.args.get("event_type", "")).strip()
        payload = build_admin_event_feed(workflow_id=workflow_id, event_type=event_type, page=page, page_size=page_size)
        payload["request_id"] = getattr(g, "request_id", "")
        return jsonify(payload)

    @app.route("/admin/workflows", methods=["GET"])
    @role_required(*admin_ops_roles)
    def admin_workflows_api():
        page = max(1, int(request.args.get("page", 1)))
        page_size = max(1, min(int(request.args.get("page_size", 20)), 100))
        decision = str(request.args.get("decision", "")).strip()
        state = str(request.args.get("state", "")).strip()
        payload = build_admin_workflow_feed(page=page, page_size=page_size, decision=decision, state=state)
        payload["request_id"] = getattr(g, "request_id", "")
        return jsonify(payload)

    @app.route("/admin/incidents", methods=["GET"])
    @role_required(*admin_ops_roles)
    def admin_incidents_api():
        payload = build_incident_console_snapshot(app.config)
        payload["request_id"] = getattr(g, "request_id", "")
        return jsonify(payload)

    @app.route("/admin/runtime/queues", methods=["GET"])
    @role_required(*admin_ops_roles)
    def admin_runtime_queues_api():
        payload = build_admin_runtime_snapshot(app.config)
        return jsonify({"queue": payload["queue"], "twilio": payload["twilio"], "redis": payload["redis"], "nats": payload["nats"], "request_id": getattr(g, "request_id", "")})

    @app.route("/admin/runtime/workers", methods=["GET"])
    @role_required(*admin_ops_roles)
    def admin_runtime_workers_api():
        payload = build_admin_runtime_snapshot(app.config)
        return jsonify({"workers": payload["workers"], "request_id": getattr(g, "request_id", "")})

    @app.route("/admin/notifications", methods=["GET"])
    @role_required(*admin_ops_roles)
    def admin_notifications_api():
        page = max(1, int(request.args.get("page", 1)))
        page_size = max(1, min(int(request.args.get("page_size", 25)), 100))
        status = str(request.args.get("status", "")).strip()
        channel = str(request.args.get("channel", "")).strip()
        payload = build_admin_notification_feed(page=page, page_size=page_size, status=status, channel=channel)
        payload["request_id"] = getattr(g, "request_id", "")
        return jsonify(payload)

    @app.route("/admin/prescriptions", methods=["GET"])
    @role_required(*admin_ops_roles)
    def admin_prescriptions_api():
        limit = max(1, min(int(request.args.get("limit", 25)), 100))
        return jsonify(
            {
                "items": [dict(row) for row in fetch_prescriptions(limit=limit)],
                "request_id": getattr(g, "request_id", ""),
            }
        )

    @app.route("/admin/notifications/<int:notification_id>/retry", methods=["POST"])
    @role_required(*admin_ops_roles)
    def admin_retry_notification_api(notification_id: int):
        result = retry_notification(notification_id, actor_name=g.user["name"], actor_role=g.user["role"])
        log_action(g.user["name"], g.user["role"], "retry-notification", "notification", notification_id, "manual operational retry queued")
        return jsonify(result)

    @app.route("/admin/audit", methods=["GET"])
    @role_required(*admin_ops_roles)
    def admin_audit_api():
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
        return jsonify(
            {
                "items": [dict(row) for row in fetch_audit_logs(limit=limit)],
                "request_id": getattr(g, "request_id", ""),
            }
        )

    @app.route("/admin/continuity", methods=["GET"])
    @role_required(*admin_ops_roles)
    def admin_continuity_api():
        payload = build_patient_continuity_snapshot(limit=max(1, min(int(request.args.get("limit", 12)), 50)))
        payload["request_id"] = getattr(g, "request_id", "")
        return jsonify(payload)

    @app.route("/admin/schedules", methods=["GET"])
    @role_required(*admin_ops_roles)
    def admin_schedule_governance_api():
        payload = build_schedule_governance_snapshot(limit=max(1, min(int(request.args.get("limit", 24)), 100)))
        payload["request_id"] = getattr(g, "request_id", "")
        return jsonify(payload)

    def _doctor_priority_label(urgency: str) -> str:
        normalized = str(urgency or "").lower()
        if normalized == "emergency":
            return "Emergency"
        if normalized in {"high", "urgent"}:
            return "High Priority"
        if normalized in {"moderate", "medium"}:
            return "Medical Review"
        return "Routine"

    def _doctor_patient_status(appointment: dict[str, object]) -> str:
        follow_up = str(appointment.get("follow_up_status") or "")
        if follow_up and follow_up != "none":
            return "Follow-Up Needed" if follow_up == "requested" else "Follow-Up Scheduled"
        status = str(appointment.get("status") or "").replace("-", " ").title()
        queue_state = str(appointment.get("queue_state") or "")
        if queue_state in {"awaiting-doctor", "assistant-review", "priority-review", "manual-review"}:
            return "Awaiting Clinical Review"
        return status or "Active"

    def _build_doctor_command_context(metrics: dict[str, object]) -> dict[str, object]:
        today = dt.date.today().isoformat()
        cases = list(metrics.get("pending_appointments", [])) + list(metrics.get("recent_cases", []))
        deduped_cases: list[dict[str, object]] = []
        seen_ids: set[int] = set()
        for item in cases:
            appointment = dict(item["appointment"])
            appointment_id = int(appointment["id"])
            if appointment_id in seen_ids:
                continue
            seen_ids.add(appointment_id)
            appointment["priority_label"] = _doctor_priority_label(str(appointment.get("urgency") or ""))
            appointment["current_status"] = _doctor_patient_status(appointment)
            deduped_cases.append({**item, "appointment": appointment})

        reports: list[dict[str, object]] = []
        care_plans: list[dict[str, object]] = []
        monitoring_alerts: list[dict[str, object]] = []
        prescriptions: list[dict[str, object]] = []
        follow_up_cases: list[dict[str, object]] = []
        emergency_cases: list[dict[str, object]] = []
        patient_cards: list[dict[str, object]] = []
        timeline: list[dict[str, object]] = []
        patient_seen: set[str] = set()

        for item in deduped_cases:
            appointment = dict(item["appointment"])
            patient_key = str(appointment.get("patient_email") or appointment.get("phone") or appointment.get("patient_name"))
            if patient_key not in patient_seen:
                patient_seen.add(patient_key)
                history = list(item.get("history", []))
                patient_cards.append(
                    {
                        "appointment": appointment,
                        "last_visit": history[0]["appointment_date"] if history else appointment.get("created_at", "")[:10],
                        "next_appointment": appointment.get("appointment_date") if str(appointment.get("appointment_date") or "") >= today else "",
                        "history_count": len(history),
                    }
                )
            if str(appointment.get("urgency")) == "Emergency":
                emergency_cases.append(item)
            if str(appointment.get("follow_up_status") or "") not in {"", "none"}:
                follow_up_cases.append(item)
            for report in item.get("report_analyses", []):
                report_item = dict(report)
                report_item["patient_name"] = appointment.get("patient_name", "")
                report_item["appointment_id"] = appointment.get("id")
                report_item["doctor_name"] = appointment.get("doctor_name", "")
                reports.append(report_item)
            for plan in item.get("care_plans", []):
                plan_item = dict(plan)
                plan_item["patient_name"] = appointment.get("patient_name", "")
                try:
                    plan_item["plan"] = json.loads(str(plan_item.get("plan_json") or "{}"))
                except (TypeError, ValueError):
                    plan_item["plan"] = {}
                care_plans.append(plan_item)
            for checkin in item.get("monitoring_checkins", []):
                checkin_item = dict(checkin)
                checkin_item["patient_name"] = appointment.get("patient_name", "")
                monitoring_alerts.append(checkin_item)
            if item.get("latest_vitals") and str(item["latest_vitals"]["risk_level"] or "").lower() not in {"", "normal"}:
                monitoring_alerts.append(
                    {
                        "patient_name": appointment.get("patient_name", ""),
                        "prompt": "Abnormal vitals",
                        "response_text": str(item["latest_vitals"]["risk_level"]),
                        "status": "needs-review",
                        "updated_at": item["latest_vitals"]["recorded_at"],
                    }
                )
            if item.get("prescription"):
                prescription = dict(item["prescription"])
                prescription["department"] = appointment.get("specialty", "")
                prescriptions.append(prescription)
            timeline.append(
                {
                    "date": str(appointment.get("appointment_date") or ""),
                    "patient_name": appointment.get("patient_name", ""),
                    "title": appointment["current_status"],
                    "details": f"{appointment.get('specialty')} · {appointment.get('slot_time') or 'Time pending'}",
                }
            )

        unread_reports = [item for item in reports if str(item.get("review_status") or "pending") != "reviewed"]
        appointments = [dict(item) for item in metrics.get("appointments", [])]
        todays_appointments = [item for item in appointments if str(item["appointment_date"]) == today]
        completed = [item for item in appointments if str(item.get("status") or "") in {"doctor-acknowledged", "completed", "follow-up"}]
        performance = {
            "patients_seen": len({str(item.get("patient_email") or item.get("phone") or item.get("patient_name")) for item in appointments}),
            "appointments_completed": len(completed),
            "average_response_time": "Same day" if completed else "Pending",
            "emergency_cases_managed": len(emergency_cases),
            "follow_up_compliance": f"{len(follow_up_cases)} active",
        }
        overview = {
            "todays_appointments": len(todays_appointments),
            "emergency_cases": len(emergency_cases),
            "pending_reviews": int(metrics.get("pending_count") or 0),
            "unread_reports": len(unread_reports),
            "follow_up_patients": len(follow_up_cases),
            "monitoring_alerts": len(monitoring_alerts),
            "department_queue": int(metrics.get("pending_count") or 0),
        }
        doctor_profile = next(
            (
                dict(row)
                for row in fetch_doctor_users(include_inactive=True)
                if str(row["doctor_name"] or row["name"]) == str(metrics.get("doctor_name") or "")
            ),
            None,
        )
        return {
            "metrics": metrics,
            "overview": overview,
            "doctor_profile": doctor_profile,
            "department": str(g.user.get("specialty") or (doctor_profile or {}).get("specialty") or "General Medicine"),
            "patient_cards": patient_cards,
            "clinical_records": deduped_cases,
            "reports": reports,
            "unread_reports": unread_reports,
            "prescriptions": prescriptions,
            "care_plans": care_plans,
            "follow_up_cases": follow_up_cases,
            "monitoring_alerts": monitoring_alerts,
            "emergency_cases": emergency_cases,
            "performance": performance,
            "timeline": sorted(timeline, key=lambda item: str(item.get("date") or ""), reverse=True),
            "availability_options": ["Available", "Busy", "On Leave", "Emergency Duty", "Offline"],
        }

    @app.route("/doctor/inbox", methods=["GET", "POST"])
    @app.route("/doctor/dashboard", methods=["GET", "POST"])
    @role_required("doctor", "clinician")
    def doctor_inbox():
        if request.method == "POST":
            action = request.form.get("action", "")
            if action == "update-availability":
                update_doctor_user(int(g.user["id"]), availability=str(request.form.get("availability", "Available")).strip())
                log_action(g.user["name"], g.user["role"], "update-availability", "doctor", int(g.user["id"]), str(request.form.get("availability", "Available")).strip())
                flash("Availability updated.", "success")
                return redirect(url_for("doctor_inbox"))
            appointment_id = int(request.form.get("appointment_id", "0"))
            if action == "acknowledge":
                update_appointment_status(appointment_id, queue_state="scheduled", status="doctor-acknowledged", acknowledged_by=g.user["name"])
                log_action(g.user["name"], g.user["role"], "acknowledge-appointment", "appointment", appointment_id, g.user["doctor_name"] or "")
                flash("Appointment acknowledged and moved to scheduled queue.", "success")
            elif action == "follow-up":
                update_appointment_status(appointment_id, follow_up_status="requested", status="follow-up", acknowledged_by=g.user["name"])
                log_action(g.user["name"], g.user["role"], "mark-follow-up", "appointment", appointment_id, "doctor requested follow-up")
                flash("Appointment marked for follow-up.", "success")
            elif action == "save-notes":
                update_doctor_notes(appointment_id, request.form.get("doctor_notes", ""), g.user["name"])
                log_action(g.user["name"], g.user["role"], "save-doctor-notes", "appointment", appointment_id, "doctor notes updated")
                flash("Doctor notes saved.", "success")
            elif action == "save-clinical-record":
                appointment = get_appointment(appointment_id)
                if appointment is None:
                    flash("Appointment not found.", "error")
                    return redirect(url_for("doctor_inbox"))
                doctor_diary = str(request.form.get("doctor_diary", "")).strip()
                prescription_text = str(request.form.get("prescription_text", "")).strip()
                if not doctor_diary and not prescription_text:
                    flash("Add patient diary notes or a prescription before saving.", "error")
                    return redirect(url_for("doctor_inbox"))
                if doctor_diary:
                    save_clinical_diary(
                        appointment_id,
                        doctor_name=str(appointment["doctor_name"]),
                        author_name=g.user["name"],
                        diary_text=doctor_diary,
                    )
                if prescription_text:
                    save_prescription_record(
                        appointment_id,
                        doctor_name=str(appointment["doctor_name"]),
                        patient_name=str(appointment["patient_name"]),
                        author_name=g.user["name"],
                        prescription_text=prescription_text,
                    )
                    notify_prescription_ready(app.config, dict(appointment), prescription_text=prescription_text)
                update_doctor_notes(appointment_id, doctor_diary or request.form.get("doctor_notes", ""), g.user["name"])
                log_action(g.user["name"], g.user["role"], "save-clinical-record", "appointment", appointment_id, "doctor diary and prescription updated")
                flash("Clinical diary saved and prescription archived.", "success")
            elif action == "save-care-plan":
                save_care_plan(
                    appointment_id,
                    doctor_name=str(g.user.get("doctor_name") or g.user["name"]),
                    medication_plan=str(request.form.get("medication_plan", "")),
                    lifestyle_guidance=str(request.form.get("lifestyle_guidance", "")),
                    diet_recommendations=str(request.form.get("diet_recommendations", "")),
                    monitoring_tasks=str(request.form.get("monitoring_tasks", "")),
                    warning_signs=str(request.form.get("warning_signs", "")),
                    follow_up_schedule=str(request.form.get("follow_up_schedule", "")),
                )
                update_appointment_status(appointment_id, follow_up_status="scheduled", status="care-plan-issued", acknowledged_by=g.user["name"])
                log_action(g.user["name"], g.user["role"], "save-care-plan", "appointment", appointment_id, "doctor care plan created")
                flash("Care plan created.", "success")
            elif action == "review-report":
                update_report_review(
                    int(request.form.get("report_id", "0")),
                    review_status=str(request.form.get("review_status") or "reviewed"),
                    review_notes=str(request.form.get("review_notes") or ""),
                )
                log_action(g.user["name"], g.user["role"], "review-report", "appointment", appointment_id, "report review updated")
                flash("Report review updated.", "success")
            elif action == "upload-report":
                try:
                    result = _process_report_upload(appointment_id)
                    flash(f"Report analyzed: {result['summary']}", "success")
                except (PermissionError, ValueError) as exc:
                    flash(str(exc), "error")
            return redirect(url_for("doctor_inbox"))
        metrics = build_doctor_metrics(g.user["doctor_name"])
        return render_template("doctor_inbox.html", **_build_doctor_command_context(metrics))

    @app.cli.command("send-reminders")
    def send_reminders_command() -> None:
        sent = send_due_reminders(app.config)
        record_automation_run("send-reminders", "completed", "Queued reminders for upcoming appointments.", sent)
        app.logger.info("Sent %s reminder batches.", sent)

    @app.cli.command("process-notifications")
    def process_notifications_command() -> None:
        processed = process_notification_queue(app.config)
        record_automation_run("process-notifications", "completed", "Processed queued notification jobs.", processed)
        app.logger.info("Processed %s queued notifications.", processed)

    @app.cli.command("seed-slots")
    def seed_slots_command() -> None:
        seed_slots(True, days=14)
        record_automation_run("seed-slots", "completed", "Seeded future slot inventory.", 14)
        app.logger.info("Seeded future doctor slots.")

    @app.cli.command("retrain-models")
    def retrain_models_command() -> None:
        train_models()
        record_automation_run("retrain-models", "completed", "Retrained routing models.", 2)
        app.logger.info("Retrained DOCQ models.")

    @app.cli.command("escalate-stale-cases")
    def escalate_stale_cases_command() -> None:
        escalated = escalate_stale_reviews()
        record_automation_run("escalate-stale-cases", "completed", "Escalated stale review cases.", escalated)
        app.logger.info("Escalated %s stale review cases.", escalated)

    @app.cli.command("seed-demo")
    def seed_demo_command() -> None:
        summary = bootstrap_demo_environment(app.config)
        record_automation_run("seed-demo", "completed", "Seeded deterministic demo environment.", int(summary.get("created_appointments", 0)))
        app.logger.info("Seeded demo environment: %s", summary)

    @app.cli.command("run-benchmarks")
    def run_benchmarks_command() -> None:
        summary = run_benchmark_suite(app.config, iterations=3)
        record_automation_run("run-benchmarks", "completed", "Ran deterministic benchmark suite.", int(summary.get("iterations", 0)))
        app.logger.info("Benchmark summary: %s", summary)

    with app.app_context():
        init_db()
        seed_users(app.config["SEED_DEMO_USERS"])
        seed_slots(app.config["SEED_SLOTS"])
        init_models(app.config["LOAD_MODELS_ON_STARTUP"])
        record_node_heartbeat(
            node_id=str(app.config.get("NODE_ID", "docq-node")),
            stream_generation=1,
            metadata={"event_bus_backend": publisher.describe().get("backend", "unknown")},
        )
        for consumer_id, subject in [
            ("projection_consumer", "projection.events"),
            ("intelligence_rollup_consumer", "workflow.events"),
            ("notification_dispatch_consumer", "notification.events"),
            ("governance_trigger_consumer", "governance.events"),
            ("telemetry_aggregation_consumer", "telemetry.events"),
        ]:
            assign_consumer_ownership(
                consumer_id=consumer_id,
                node_id=str(app.config.get("NODE_ID", "docq-node")),
                stream_subject=subject,
                lease_token=f"{app.config.get('NODE_ID', 'docq-node')}:{consumer_id}",
                ownership_generation=1,
            )

    return app
