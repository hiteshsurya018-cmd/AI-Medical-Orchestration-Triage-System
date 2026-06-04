from __future__ import annotations

import datetime as dt
import secrets
from functools import wraps
from urllib.parse import urljoin, urlparse

from flask import current_app, flash, g, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .compliance import encrypt_sensitive_value
from .constants import DOCTOR_ACCOUNTS
from .db import get_connection

ENTERPRISE_ROLE_DEFAULTS = {
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
}


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    return check_password_hash(password_hash, password)


def seed_users(enabled: bool) -> None:
    if not enabled:
        return
    created_at = dt.datetime.now().isoformat(timespec="seconds")
    core_users = [
        ("Clinic Admin", "admin@docq.local", encrypt_sensitive_value("admin@docq.local"), hash_password("admin123"), "admin", "default-clinic", "central-admin", "Mysore Central", None, None, None, None, created_at, created_at),
        ("Front Desk", "desk@docq.local", encrypt_sensitive_value("desk@docq.local"), hash_password("desk123"), "receptionist", "default-clinic", "front-desk", "Mysore Central", None, None, None, None, created_at, created_at),
        ("Governance Analyst", "governance@docq.local", encrypt_sensitive_value("governance@docq.local"), hash_password("governance123"), "governance_analyst", "default-clinic", "governance", "Mysore Central", None, None, None, None, created_at, created_at),
        ("Read Only Auditor", "auditor@docq.local", encrypt_sensitive_value("auditor@docq.local"), hash_password("auditor123"), "auditor", "default-clinic", "audit", "Mysore Central", None, None, None, None, created_at, created_at),
        ("Clinic Admin Scoped", "clinicadmin@docq.local", encrypt_sensitive_value("clinicadmin@docq.local"), hash_password("clinic123"), "clinic_admin", "default-clinic", "clinic-admin", "Mysore Central", None, None, None, None, created_at, created_at),
        ("Compliance Officer", "compliance@docq.local", encrypt_sensitive_value("compliance@docq.local"), hash_password("compliance123"), "compliance_officer", "default-clinic", "compliance", "Mysore Central", None, None, None, None, created_at, created_at),
        ("Aarav Patient", "patient@docq.local", encrypt_sensitive_value("patient@docq.local"), hash_password("patient123"), "patient", "default-clinic", "patient", None, None, None, "7000000000", encrypt_sensitive_value("7000000000"), created_at, created_at),
    ]
    with get_connection() as connection:
        for user in core_users:
            connection.execute(
                """
                INSERT OR IGNORE INTO users (name, email, email_encrypted, password_hash, role, tenant_key, org_unit, branch, doctor_name, specialty, phone, phone_encrypted, email_verified_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                user,
            )
        for doctor in DOCTOR_ACCOUNTS:
            connection.execute(
                """
                INSERT OR IGNORE INTO users (name, email, email_encrypted, password_hash, role, tenant_key, org_unit, branch, doctor_name, specialty, phone, phone_encrypted, email_verified_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doctor["name"],
                    doctor["email"],
                    encrypt_sensitive_value(doctor["email"]),
                    hash_password(doctor["password"]),
                    doctor["role"],
                    "default-clinic",
                    doctor["specialty"],
                    doctor["branch"],
                    doctor["doctor_name"],
                    doctor["specialty"],
                    doctor.get("phone"),
                    encrypt_sensitive_value(str(doctor.get("phone"))) if doctor.get("phone") else None,
                    created_at,
                    created_at,
                ),
            )
        for email, role_scope in [
            ("admin@docq.local", "admin"),
            ("desk@docq.local", "operations"),
            ("governance@docq.local", "governance"),
            ("auditor@docq.local", "audit"),
            ("clinicadmin@docq.local", "clinic_admin"),
            ("compliance@docq.local", "compliance"),
        ]:
            user_row = connection.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if user_row is not None:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO tenant_memberships (user_id, tenant_key, role_scope, org_unit, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (int(user_row["id"]), "default-clinic", role_scope, role_scope, created_at),
                )
        connection.execute(
            """
            INSERT OR IGNORE INTO patient_profiles (
                patient_name, patient_email, patient_email_encrypted, phone, phone_encrypted, patient_age, chronic_conditions, chronic_conditions_encrypted, allergies, allergies_encrypted, tenant_key, last_visit_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Aarav Patient",
                "patient@docq.local",
                encrypt_sensitive_value("patient@docq.local"),
                "7000000000",
                encrypt_sensitive_value("7000000000"),
                62,
                "hypertension, asthma",
                encrypt_sensitive_value("hypertension, asthma"),
                "penicillin",
                encrypt_sensitive_value("penicillin"),
                "default-clinic",
                created_at[:10],
                created_at,
                created_at,
            ),
        )


def get_user_by_email(email: str):
    with get_connection() as connection:
        return connection.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()


def get_doctor_user(doctor_name: str):
    with get_connection() as connection:
        return connection.execute("SELECT * FROM users WHERE doctor_name = ?", (doctor_name,)).fetchone()


def get_user_by_id(user_id: int):
    with get_connection() as connection:
        return connection.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()


def _login_endpoint_for_request() -> str:
    return "login"


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentication required"}), 401
            return redirect(url_for(_login_endpoint_for_request(), next=request.path))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "authentication required"}), 401
                return redirect(url_for(_login_endpoint_for_request(), next=request.path))
            if session.get("role") not in roles:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "forbidden"}), 403
                flash("You do not have access to that DOCQ view.", "error")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def tenant_role_required(*roles):
    return role_required(*roles)


def load_current_user() -> None:
    g.user = None
    if "user_id" in session:
        issued_at = session.get("issued_at")
        ttl_minutes = int(current_app.config.get("SESSION_TTL_MINUTES", 120))
        if issued_at:
            try:
                issued_dt = dt.datetime.fromisoformat(str(issued_at))
                if (dt.datetime.now() - issued_dt) > dt.timedelta(minutes=ttl_minutes):
                    session.clear()
                    return
            except ValueError:
                session.clear()
                return
        g.user = {
            "id": session.get("user_id"),
            "name": session.get("user_name"),
            "role": session.get("role"),
            "email": session.get("user_email"),
            "tenant_key": session.get("tenant_key"),
            "org_unit": session.get("org_unit"),
            "doctor_name": session.get("doctor_name"),
            "branch": session.get("branch"),
            "specialty": session.get("specialty"),
        }


def generate_csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf() -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    if request.endpoint == "static":
        return
    sent_token = request.headers.get("X-CSRF-Token") or request.form.get("_csrf_token")
    expected = session.get("_csrf_token")
    if not expected or not sent_token or not secrets.compare_digest(expected, sent_token):
        raise PermissionError("Invalid CSRF token.")


def is_safe_redirect_target(target: str | None) -> bool:
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in {"http", "https"} and ref_url.netloc == test_url.netloc


def login_user(user, *, remember_email: bool = True) -> None:
    remembered_email = session.get("remembered_email") if remember_email else None
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    session["role"] = user["role"]
    session["user_email"] = user["email"]
    session["tenant_key"] = user["tenant_key"]
    session["org_unit"] = user["org_unit"]
    session["doctor_name"] = user["doctor_name"]
    session["branch"] = user["branch"]
    session["specialty"] = user["specialty"]
    session["issued_at"] = dt.datetime.now().isoformat(timespec="seconds")
    session["_csrf_token"] = secrets.token_urlsafe(32)
    if remember_email:
        session["remembered_email"] = str(user["email"] or remembered_email or "")


def create_user(
    *,
    name: str,
    email: str,
    password: str,
    role: str = "patient",
    branch: str | None = None,
    tenant_key: str = "default-clinic",
    org_unit: str | None = None,
    phone: str | None = None,
    doctor_name: str | None = None,
    specialty: str | None = None,
    specialization: str | None = None,
    status: str = "active",
    availability: str = "Available",
    email_verified: bool = False,
) -> int:
    created_at = dt.datetime.now().isoformat(timespec="seconds")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO users (
                name, email, email_encrypted, password_hash, role, tenant_key, org_unit, branch,
                doctor_name, specialty, specialization, status, availability, phone, phone_encrypted,
                email_verified_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name.strip(),
                email.strip().lower(),
                encrypt_sensitive_value(email.strip().lower()),
                hash_password(password),
                role,
                tenant_key,
                org_unit or specialty,
                branch,
                doctor_name.strip() if doctor_name else None,
                specialty.strip() if specialty else None,
                specialization.strip() if specialization else None,
                status.strip() or "active",
                availability.strip() or "Available",
                phone.strip() if phone else None,
                encrypt_sensitive_value(phone.strip()) if phone and phone.strip() else None,
                created_at if email_verified else None,
                created_at,
            ),
        )
        user_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT OR IGNORE INTO tenant_memberships (user_id, tenant_key, role_scope, org_unit, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, tenant_key, role, org_unit or role, created_at),
        )
        return user_id


def issue_auth_token(*, user_id: int, token_type: str, expires_minutes: int = 60) -> str:
    token = secrets.token_urlsafe(24)
    created_at = dt.datetime.now().isoformat(timespec="seconds")
    expires_at = (dt.datetime.now() + dt.timedelta(minutes=expires_minutes)).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO auth_tokens (user_id, token, token_type, status, created_at, expires_at)
            VALUES (?, ?, ?, 'issued', ?, ?)
            """,
            (int(user_id), token, token_type, created_at, expires_at),
        )
    return token


def consume_auth_token(token: str, token_type: str):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT * FROM auth_tokens
            WHERE token = ? AND token_type = ? AND status = 'issued'
            ORDER BY id DESC
            LIMIT 1
            """,
            (token, token_type),
        ).fetchone()
        if row is None:
            return None
        expires_at = row["expires_at"]
        if expires_at and dt.datetime.fromisoformat(str(expires_at)) < dt.datetime.now():
            connection.execute("UPDATE auth_tokens SET status = 'expired' WHERE id = ?", (int(row["id"]),))
            return None
        consumed_at = dt.datetime.now().isoformat(timespec="seconds")
        connection.execute(
            "UPDATE auth_tokens SET status = 'consumed', consumed_at = ? WHERE id = ?",
            (consumed_at, int(row["id"])),
        )
        return row


def mark_user_email_verified(user_id: int) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE users SET email_verified_at = ? WHERE id = ?",
            (dt.datetime.now().isoformat(timespec="seconds"), int(user_id)),
        )


def update_user_password(user_id: int, password: str) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(password), int(user_id)),
        )


def inject_globals() -> dict[str, object]:
    return {
        "current_user": g.get("user"),
        "csrf_token": generate_csrf_token,
        "remembered_email": session.get("remembered_email", ""),
    }
