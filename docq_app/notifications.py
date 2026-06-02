from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from urllib import error, parse, request as urlrequest

from flask import current_app, has_app_context

from .auth import get_doctor_user
from .tenancy import get_current_tenant_key
from .db import get_connection, _runtime_config
from .worker_runtime import enqueue_job, worker_execution_repository

logger = logging.getLogger(__name__)

RETRY_DELAYS_MINUTES = [5, 15, 60]


def _fallback_channel(row) -> str | None:
    if row["channel"] == "sms" and row["patient_email"]:
        return "email"
    if row["channel"] in {"sms", "email", "whatsapp"}:
        return "dashboard"
    return None


def _is_confirmation_notification(row) -> bool:
    return row["target_type"] == "patient" and row["channel"] == "sms" and str(row["message"]).startswith("DOCQ confirmed your appointment")


def send_confirmation_to_n8n(config: dict[str, object], row) -> tuple[str, str | None, str | None]:
    webhook_url = config.get("DOCQ_N8N_CONFIRMATION_WEBHOOK")
    if not webhook_url:
        return "config-missing", None, "n8n-webhook-missing"

    payload = {
        "patient_name": row["target_name"],
        "phone": row["phone"],
        "doctor_name": row["doctor_name"],
        "appointment_date": row["appointment_date"],
        "slot_time": row["slot_time"],
        "channel": row["channel"],
    }
    request_obj = urlrequest.Request(
        str(webhook_url),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(request_obj, timeout=15) as response:
            response_body = response.read().decode("utf-8", errors="ignore")
            external_id = None
            if response_body:
                try:
                    parsed = json.loads(response_body)
                    external_id = parsed.get("message_sid") or parsed.get("executionId")
                except json.JSONDecodeError:
                    external_id = None
        return "sent", external_id, None
    except error.HTTPError as exc:
        logger.warning("n8n confirmation webhook failed with HTTP status %s", exc.code)
        return f"failed:{exc.code}", None, f"n8n-http-{exc.code}"
    except Exception:
        logger.exception("n8n confirmation webhook failed unexpectedly.")
        return "failed", None, "n8n-request-failed"


def delivery_configs(config: dict[str, object]) -> dict[str, bool]:
    return {
        "sms": bool(config["TWILIO_ACCOUNT_SID"] and config["TWILIO_AUTH_TOKEN"] and config["TWILIO_FROM_NUMBER"]),
        "whatsapp": bool(config["TWILIO_ACCOUNT_SID"] and config["TWILIO_AUTH_TOKEN"] and config["TWILIO_WHATSAPP_FROM"]),
        "email": bool(config["SMTP_HOST"] and config["SMTP_USERNAME"] and config["SMTP_PASSWORD"] and config["SMTP_FROM"]),
    }


def normalize_phone_number(phone: str) -> str:
    raw = str(phone or "").strip()
    if not raw:
        return ""
    if raw.startswith("whatsapp:"):
        raw = raw.split(":", 1)[1].strip()
    if raw.startswith("+"):
        digits = re.sub(r"\D", "", raw)
        return f"+{digits}" if digits else ""
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    if len(digits) == 11 and digits.startswith("0"):
        return f"+91{digits[1:]}"
    return f"+{digits}"


def send_twilio_message(config: dict[str, object], to_number: str, body: str, *, whatsapp: bool = False) -> tuple[str, str | None]:
    sid = config["TWILIO_ACCOUNT_SID"]
    token = config["TWILIO_AUTH_TOKEN"]
    from_number = config["TWILIO_WHATSAPP_FROM" if whatsapp else "TWILIO_FROM_NUMBER"]
    normalized_to = normalize_phone_number(to_number)
    if not sid or not token or not from_number or not normalized_to:
        return "config-missing", None

    payload = {"To": f"whatsapp:{normalized_to}" if whatsapp else normalized_to, "From": from_number, "Body": body}
    encoded = parse.urlencode(payload).encode()
    req = urlrequest.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=encoded,
        headers={
            "Authorization": "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode())
        return "sent", data.get("sid")
    except error.HTTPError as exc:
        logger.warning("Twilio delivery failed with HTTP status %s", exc.code)
        return f"failed:{exc.code}", None
    except Exception:
        logger.exception("Twilio delivery failed unexpectedly.")
        return "failed", None


def send_email_message(config: dict[str, object], to_email: str, subject: str, body: str) -> tuple[str, str | None]:
    host = config["SMTP_HOST"]
    port = config["SMTP_PORT"]
    username = config["SMTP_USERNAME"]
    password = config["SMTP_PASSWORD"]
    sender = config["SMTP_FROM"]
    if not host or not username or not password or not sender or not to_email:
        return "config-missing", None

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to_email
    message.set_content(body)

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15) as smtp:
                smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(username, password)
                smtp.send_message(message)
        return "sent", None
    except Exception:
        logger.exception("Email delivery failed unexpectedly.")
        return "failed", None


def create_notification(
    appointment_id: int | None,
    target_type: str,
    target_name: str,
    channel: str,
    message: str,
    status: str = "queued",
    external_id: str | None = None,
    attempt_count: int = 0,
    next_attempt_at: str | None = None,
    last_error: str | None = None,
    tenant_key: str | None = None,
    correlation_id: str | None = None,
    provider_metadata: dict[str, object] | None = None,
    message_category: str | None = None,
) -> int:
    tenant_key = tenant_key or get_current_tenant_key()
    provider_metadata_json = json.dumps(provider_metadata or {}, sort_keys=True)
    print(
        "[NOTIFICATION CREATE]",
        {
            "appointment_id": int(appointment_id) if appointment_id else None,
            "target_type": str(target_type),
            "target_name": str(target_name),
            "channel": str(channel),
            "status": str(status),
            "correlation_id": str(correlation_id or ""),
            "message_category": str(message_category or ""),
        },
        flush=True,
    )
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO notifications (
                appointment_id, tenant_key, target_type, target_name, channel, message, status,
                external_id, correlation_id, attempt_count, next_attempt_at, last_error,
                acknowledged_at, provider_metadata_json, message_category, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                appointment_id,
                tenant_key,
                target_type,
                target_name,
                channel,
                message,
                status,
                external_id,
                correlation_id,
                attempt_count,
                next_attempt_at,
                last_error,
                None,
                provider_metadata_json,
                message_category,
                dt.datetime.now().isoformat(timespec="seconds"),
            ),
        )
        notification_id = int(cursor.lastrowid)
    _maybe_enqueue_notification_dispatch(
        notification_id=notification_id,
        appointment_id=appointment_id,
        channel=channel,
        status=status,
        tenant_key=tenant_key,
    )
    return notification_id


def _dispatch_notification_inline(notification_id: int) -> None:
    if not has_app_context():
        print("[INLINE DISPATCH SKIP] missing_app_context", flush=True)
        return
    try:
        print(f"[INLINE DISPATCH EXECUTE] notification={int(notification_id)}", flush=True)
        dispatch_notification_job(int(notification_id))
        print(f"[INLINE DISPATCH SUCCESS] notification={int(notification_id)}", flush=True)
    except Exception:
        print(f"[INLINE DISPATCH ERROR] notification={int(notification_id)}", flush=True)
        logger.exception("Inline notification dispatch failed for notification %s.", notification_id)


def _maybe_enqueue_notification_dispatch(*, notification_id: int, appointment_id: int | None, channel: str, status: str, tenant_key: str) -> None:
    print(
        "[ENQUEUE DEBUG START]",
        {
            "notification_id": int(notification_id),
            "appointment_id": int(appointment_id) if appointment_id else None,
            "channel": str(channel),
            "status": str(status),
            "tenant_key": str(tenant_key),
        },
        flush=True,
    )
    if status != "queued" or channel not in {"sms", "whatsapp", "email"}:
        if status != "queued":
            print(
                "[ENQUEUE SKIP] status_not_queued",
                status,
                flush=True,
            )
        else:
            print(
                "[ENQUEUE SKIP] unsupported_channel",
                channel,
                flush=True,
            )
        return
    if not has_app_context():
        print("[ENQUEUE SKIP] missing_app_context", flush=True)
        return
    if not bool(current_app.config.get("ENABLE_WORKERS")):
        print("[ENQUEUE SKIP] workers_disabled", flush=True)
        _dispatch_notification_inline(notification_id)
        return
    redis_url = str(current_app.config.get("REDIS_URL", "") or "")
    if not redis_url:
        print("[ENQUEUE SKIP] missing_redis_url", flush=True)
        _dispatch_notification_inline(notification_id)
        return
    try:
        print(
            f"[ENQUEUE EXECUTE] notification={int(notification_id)} queue=docq-default redis_url={redis_url}",
            flush=True,
        )
        enqueue_job(
            redis_url,
            "docq_app.notifications.dispatch_notification_job",
            notification_id,
            idempotency_key=f"notification-dispatch:{notification_id}",
            workflow_id=f"appointment-lifecycle:{appointment_id}" if appointment_id else f"notification:{notification_id}",
        )
        print(
            f"[ENQUEUE SUCCESS] notification={int(notification_id)}",
            flush=True,
        )
    except Exception:
        print(
            f"[ENQUEUE ERROR] notification={int(notification_id)}",
            flush=True,
        )
        logger.exception("Failed to enqueue notification dispatch job for notification %s.", notification_id)
        _dispatch_notification_inline(notification_id)


def _notification_row_query() -> str:
    return """
        SELECT n.id, n.appointment_id, n.target_type, n.target_name, n.channel, n.message, n.status,
               n.correlation_id, n.attempt_count, n.next_attempt_at, a.phone, a.patient_email, a.doctor_name,
               a.appointment_date, a.slot_time
        FROM notifications n
        LEFT JOIN appointments a ON a.id = n.appointment_id
    """


def _fetch_notification_row(notification_id: int):
    with get_connection() as connection:
        return connection.execute(
            f"{_notification_row_query()} WHERE n.id = ? LIMIT 1",
            (int(notification_id),),
        ).fetchone()


def _process_notification_row(config: dict[str, object], row) -> str:
    print(
        "[WORKER NOTIFICATION FOUND]",
        {
            "notification_id": int(row["id"]),
            "channel": str(row["channel"]),
            "status": str(row["status"]),
            "target_type": str(row["target_type"]),
            "appointment_id": int(row["appointment_id"]) if row["appointment_id"] else None,
        },
        flush=True,
    )
    target_email = row["patient_email"]
    target_phone = row["phone"]
    if row["target_type"] == "doctor":
        doctor_user = get_doctor_user(row["doctor_name"] or row["target_name"])
        target_email = doctor_user["email"] if doctor_user else None
        target_phone = doctor_user["phone"] if doctor_user and "phone" in doctor_user.keys() else None
    print(
        "[WORKER SEND ATTEMPT]",
        {
            "notification_id": int(row["id"]),
            "channel": str(row["channel"]),
            "target_phone": str(target_phone or ""),
            "target_email": str(target_email or ""),
            "path": "n8n" if _is_confirmation_notification(row) else "direct",
        },
        flush=True,
    )
    try:
        if _is_confirmation_notification(row):
            status, external_id, last_error = send_confirmation_to_n8n(config, row)
        else:
            status, external_id, last_error = deliver_notification(
                config,
                channel=row["channel"],
                phone=target_phone,
                email=target_email,
                message=row["message"],
                email_subject="DOCQ update",
                whatsapp=row["channel"] == "whatsapp",
            )
        print(
            "[WORKER SEND RESULT]",
            {
                "notification_id": int(row["id"]),
                "channel": str(row["channel"]),
                "status": str(status),
                "external_id": str(external_id or ""),
                "last_error": str(last_error or ""),
            },
            flush=True,
        )
    except Exception as exc:
        print(
            "[WORKER SEND ERROR]",
            {
                "notification_id": int(row["id"]),
                "channel": str(row["channel"]),
                "error": str(exc),
            },
            flush=True,
        )
        raise
    attempts = int(row["attempt_count"]) + 1
    next_attempt_at = None
    final_status = status
    if status != "sent" and attempts <= len(RETRY_DELAYS_MINUTES):
        final_status = "retry"
        next_attempt_at = (dt.datetime.now() + dt.timedelta(minutes=RETRY_DELAYS_MINUTES[attempts - 1])).isoformat(timespec="seconds")
    elif status != "sent":
        final_status = "failed"
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE notifications
            SET status = ?, external_id = COALESCE(?, external_id), attempt_count = ?, next_attempt_at = ?, last_error = ?,
                acknowledged_at = CASE WHEN ? = 'sent' THEN ? ELSE acknowledged_at END,
                provider_metadata_json = ?
            WHERE id = ?
            """,
            (
                final_status,
                external_id,
                attempts,
                next_attempt_at,
                last_error,
                final_status,
                dt.datetime.now().isoformat(timespec="seconds") if final_status == "sent" else None,
                json.dumps({"channel": row["channel"], "external_id": external_id, "last_error": last_error}, sort_keys=True),
                row["id"],
            ),
        )
    if row["appointment_id"]:
        from .appointments import record_workflow_event

        record_workflow_event(
            f"appointment-lifecycle:{row['appointment_id']}",
            trace_id=f"appointment-lifecycle:{row['appointment_id']}",
            correlation_id=str(row["appointment_id"]),
            stage="notification-runtime",
            agent="notification-runtime",
            action=f"notification_{final_status}_{row['channel']}",
            decision=final_status,
            confidence=100.0 if final_status == "sent" else 65.0,
            reasons=[f"{row['channel']} delivery {final_status}"],
            payload={
                "appointment_id": int(row["appointment_id"]),
                "channel": row["channel"],
                "correlation_id": row["correlation_id"],
                "external_id": external_id,
                "attempts": attempts,
                "last_error": last_error,
            },
        )
    if final_status == "failed":
        fallback_channel = _fallback_channel(row)
        if fallback_channel == "email":
            create_notification(
                row["appointment_id"],
                row["target_type"],
                row["target_name"],
                "email",
                row["message"],
                "queued",
                last_error=f"fallback-from-{row['channel']}",
                correlation_id=f"{row['correlation_id']}:fallback-email" if row["correlation_id"] else None,
                message_category="delivery_recovery",
            )
        elif fallback_channel == "dashboard":
            create_notification(
                row["appointment_id"],
                row["target_type"],
                row["target_name"],
                "dashboard",
                f"Delivery recovery required after {row['channel']} failure: {row['message']}",
                "visible",
                last_error=f"fallback-from-{row['channel']}",
                correlation_id=f"{row['correlation_id']}:fallback-dashboard" if row["correlation_id"] else None,
                message_category="delivery_recovery",
            )
    return final_status


def dispatch_notification_job(notification_id: int) -> dict[str, object]:
    task_id = f"queued:notification-dispatch:{int(notification_id)}"
    print("[WORKER JOB START]", {"notification_id": int(notification_id), "task_id": task_id}, flush=True)
    worker_execution_repository.mark_execution_state(task_id=task_id, status="running")
    row = _fetch_notification_row(int(notification_id))
    if row is None:
        print("[WORKER JOB MISSING]", {"notification_id": int(notification_id)}, flush=True)
        worker_execution_repository.mark_execution_state(task_id=task_id, status="missing")
        return {"notification_id": int(notification_id), "status": "missing"}
    if row["status"] not in {"queued", "retry"}:
        print(
            "[WORKER JOB SKIP]",
            {"notification_id": int(notification_id), "current_status": str(row["status"])},
            flush=True,
        )
        worker_execution_repository.mark_execution_state(task_id=task_id, status="skipped")
        return {"notification_id": int(notification_id), "status": "skipped", "current_status": row["status"]}
    config = dict(current_app.config) if has_app_context() else _runtime_config()
    final_status = _process_notification_row(config, row)
    print(
        "[WORKER JOB COMPLETE]",
        {"notification_id": int(notification_id), "final_status": str(final_status)},
        flush=True,
    )
    worker_execution_repository.mark_execution_state(
        task_id=task_id,
        status="completed" if final_status == "sent" else final_status,
    )
    return {"notification_id": int(notification_id), "status": final_status}


def deliver_notification(
    config: dict[str, object],
    *,
    channel: str,
    phone: str | None = None,
    email: str | None = None,
    message: str,
    email_subject: str | None = None,
    whatsapp: bool = False,
) -> tuple[str, str | None, str | None]:
    if channel == "email":
        status, external_id = send_email_message(config, email or "", email_subject or "DOCQ update", message)
        return status, external_id, None if status == "sent" else status
    status, external_id = send_twilio_message(config, phone or "", message, whatsapp=whatsapp or channel == "whatsapp")
    return status, external_id, None if status == "sent" else status


def notify_automation(config: dict[str, object], appointment: dict[str, object]) -> None:
    from .appointments import get_patient_profile

    print(
        "[NOTIFY AUTOMATION START]",
        {
            "appointment_id": int(appointment["id"]),
            "patient_name": str(appointment["patient_name"]),
            "doctor_name": str(appointment["doctor_name"]),
        },
        flush=True,
    )
    doctor_user = get_doctor_user(str(appointment["doctor_name"]))
    patient_profile = get_patient_profile(phone=str(appointment.get("phone", "")), patient_email=str(appointment.get("patient_email", "")))
    preferences = json.loads(patient_profile["communication_preferences_json"] or "{}") if patient_profile and patient_profile["communication_preferences_json"] else {}
    prefer_whatsapp = bool(preferences.get("whatsapp", bool(appointment.get("phone"))))
    doctor_whatsapp = bool(doctor_user and doctor_user["phone"])
    print(
        "[NOTIFY AUTOMATION PROFILE]",
        {
            "appointment_id": int(appointment["id"]),
            "prefer_whatsapp": prefer_whatsapp,
            "doctor_whatsapp": doctor_whatsapp,
            "has_patient_email": bool(appointment.get("patient_email")),
            "doctor_user_found": bool(doctor_user),
        },
        flush=True,
    )
    doctor_message = (
        f"New DOCQ case: {appointment['patient_name']} assigned to {appointment['doctor_name']} on "
        f"{appointment['appointment_date']} at {appointment['slot_time']}."
    )
    patient_message = (
        f"DOCQ confirmed your appointment with {appointment['doctor_name']} on "
        f"{appointment['appointment_date']} at {appointment['slot_time']}."
    )
    dashboard_message = f"{appointment['patient_name']} moved into {appointment['queue_state']} at {appointment['branch']}."

    correlation_id = f"appointment:{appointment['id']}:confirmation"
    create_notification(appointment["id"], "operations", "Front Desk", "dashboard", dashboard_message, "visible", correlation_id=correlation_id, message_category="operations_update")
    if doctor_user:
        create_notification(
            appointment["id"],
            "doctor",
            str(appointment["doctor_name"]),
            "email",
            doctor_message,
            "queued",
            correlation_id=f"appointment:{appointment['id']}:doctor-assignment",
            message_category="doctor_assignment",
        )
        if doctor_whatsapp:
            create_notification(
                appointment["id"],
                "doctor",
                str(appointment["doctor_name"]),
                "whatsapp",
                f"DOCQ assignment: {appointment['patient_name']} scheduled {appointment['appointment_date']} at {appointment['slot_time']}.",
                "queued",
                correlation_id=f"appointment:{appointment['id']}:doctor-whatsapp",
                message_category="doctor_assignment",
            )
    else:
        create_notification(appointment["id"], "doctor", str(appointment["doctor_name"]), "dashboard", doctor_message, "visible", correlation_id=f"appointment:{appointment['id']}:doctor-dashboard", message_category="doctor_assignment")

    create_notification(
        appointment["id"],
        "patient",
        str(appointment["patient_name"]),
        "sms",
        patient_message,
        "queued",
        correlation_id=correlation_id,
        message_category="appointment_confirmation",
    )
    if prefer_whatsapp:
        create_notification(
            appointment["id"],
            "patient",
            str(appointment["patient_name"]),
            "whatsapp",
            patient_message,
            "queued",
            correlation_id=f"appointment:{appointment['id']}:whatsapp-confirmation",
            message_category="appointment_confirmation",
        )
    if appointment.get("patient_email"):
        create_notification(
            appointment["id"],
            "patient",
            str(appointment["patient_name"]),
            "email",
            patient_message,
            "queued",
            correlation_id=f"appointment:{appointment['id']}:email-confirmation",
            message_category="appointment_confirmation",
        )


def notify_prescription_ready(config: dict[str, object], appointment: dict[str, object], *, prescription_text: str) -> None:
    summary = " ".join(line.strip() for line in str(prescription_text).splitlines() if line.strip())
    if len(summary) > 900:
        summary = f"{summary[:897]}..."
    patient_message = (
        f"DOCQ prescription from {appointment['doctor_name']} for {appointment['patient_name']}: {summary}"
    )
    admin_message = (
        f"Prescription archived for {appointment['patient_name']} under appointment {appointment['id']} by {appointment['doctor_name']}."
    )
    create_notification(
        int(appointment["id"]),
        "operations",
        "Front Desk",
        "dashboard",
        admin_message,
        "visible",
        correlation_id=f"appointment:{appointment['id']}:prescription-archive",
        message_category="prescription_archive",
    )
    create_notification(
        int(appointment["id"]),
        "patient",
        str(appointment["patient_name"]),
        "whatsapp",
        patient_message,
        "queued",
        correlation_id=f"appointment:{appointment['id']}:prescription-whatsapp",
        message_category="prescription_delivery",
    )
    if appointment.get("patient_email"):
        create_notification(
            int(appointment["id"]),
            "patient",
            str(appointment["patient_name"]),
            "email",
            patient_message,
            "queued",
            correlation_id=f"appointment:{appointment['id']}:prescription-email",
            message_category="prescription_delivery",
        )


def process_notification_queue(config: dict[str, object], batch_size: int = 25) -> int:
    now = dt.datetime.now().isoformat(timespec="seconds")
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            {_notification_row_query()}
            WHERE n.status IN ('queued', 'retry')
              AND (n.next_attempt_at IS NULL OR n.next_attempt_at <= ?)
            ORDER BY n.created_at ASC
            LIMIT ?
            """,
            (now, batch_size),
        ).fetchall()

    processed = 0
    for row in rows:
        _process_notification_row(config, row)
        processed += 1
    return processed


def send_due_reminders(config: dict[str, object]) -> int:
    target_date = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with get_connection() as connection:
        appointments = connection.execute(
            """
            SELECT *
            FROM appointments
            WHERE appointment_date = ?
              AND reminder_sent = 0
              AND status IN ('scheduled', 'doctor-acknowledged')
            """,
            (target_date,),
        ).fetchall()

    for appointment in appointments:
        message = (
            f"Reminder from DOCQ: {appointment['patient_name']} has an appointment with "
            f"{appointment['doctor_name']} tomorrow at {appointment['slot_time']}."
        )
        create_notification(appointment["id"], "patient", appointment["patient_name"], "sms", message, "queued", correlation_id=f"appointment:{appointment['id']}:sms-reminder", message_category="appointment_reminder")
        create_notification(appointment["id"], "patient", appointment["patient_name"], "whatsapp", message, "queued", correlation_id=f"appointment:{appointment['id']}:whatsapp-reminder", message_category="appointment_reminder")
        if appointment["patient_email"]:
            create_notification(appointment["id"], "patient", appointment["patient_name"], "email", message, "queued", correlation_id=f"appointment:{appointment['id']}:email-reminder", message_category="appointment_reminder")
        with get_connection() as connection:
            connection.execute("UPDATE appointments SET reminder_sent = 1 WHERE id = ?", (appointment["id"],))
    return len(appointments)


def retry_notification(notification_id: int, *, actor_name: str = "", actor_role: str = "") -> dict[str, object]:
    row = _fetch_notification_row(int(notification_id))
    if row is None:
        raise LookupError(f"notification {notification_id} not found")
    now = dt.datetime.now().isoformat(timespec="seconds")
    metadata = {"manual_retry_requested_at": now, "manual_retry_actor": actor_name, "manual_retry_role": actor_role}
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE notifications
            SET status = 'queued', next_attempt_at = NULL, last_error = NULL, provider_metadata_json = ?
            WHERE id = ?
            """,
            (json.dumps(metadata, sort_keys=True), int(notification_id)),
        )
    if row["appointment_id"]:
        from .appointments import record_workflow_event

        record_workflow_event(
            f"appointment-lifecycle:{row['appointment_id']}",
            trace_id=f"appointment-lifecycle:{row['appointment_id']}",
            correlation_id=str(row["appointment_id"]),
            stage="notification-runtime",
            agent="notification-runtime",
            action="notification_retry_requested",
            decision="queued",
            confidence=100.0,
            reasons=[f"manual retry requested by {actor_name or 'operator'}"],
            payload={
                "appointment_id": int(row["appointment_id"]),
                "notification_id": int(notification_id),
                "channel": row["channel"],
                "correlation_id": row["correlation_id"],
                "actor_name": actor_name,
                "actor_role": actor_role,
            },
        )
    _maybe_enqueue_notification_dispatch(
        notification_id=int(notification_id),
        appointment_id=int(row["appointment_id"]) if row["appointment_id"] else None,
        channel=str(row["channel"]),
        status="queued",
        tenant_key=get_current_tenant_key(),
    )
    return {"notification_id": int(notification_id), "status": "queued", "retried_at": now}
