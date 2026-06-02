from __future__ import annotations

import datetime as dt
import json

from flask import current_app, g, has_app_context

from .db import get_connection

ENTERPRISE_ROLES = {
    "hospital_admin",
    "clinic_admin",
    "governance_reviewer",
    "compliance_officer",
    "operations_manager",
    "department_supervisor",
    "auditor",
}


def get_current_tenant_key() -> str:
    if has_app_context() and g.get("user") and g.user.get("tenant_key"):
        return str(g.user["tenant_key"])
    if has_app_context():
        return str(current_app.config.get("DEFAULT_TENANT_KEY", "default-clinic"))
    return "default-clinic"


def ensure_default_tenant(tenant_key: str = "default-clinic") -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO tenants (tenant_key, tenant_name, tenant_type, parent_tenant_key, status, encryption_context, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (tenant_key, "DOCQ Default Clinic", "clinic", None, "active", json.dumps({"scope": "default"}, sort_keys=True), dt.datetime.now().isoformat(timespec="seconds")),
        )


def create_tenant(*, tenant_key: str, tenant_name: str, tenant_type: str, parent_tenant_key: str | None = None) -> dict[str, object]:
    created_at = dt.datetime.now().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO tenants (tenant_key, tenant_name, tenant_type, parent_tenant_key, status, encryption_context, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (tenant_key, tenant_name, tenant_type, parent_tenant_key, "active", json.dumps({"scope": tenant_key}, sort_keys=True), created_at),
        )
    return {"tenant_key": tenant_key, "tenant_name": tenant_name, "tenant_type": tenant_type, "created_at": created_at}


def assign_user_tenant_role(*, user_id: int, tenant_key: str, role_scope: str, org_unit: str = "") -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO tenant_memberships (user_id, tenant_key, role_scope, org_unit, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, tenant_key, role_scope, org_unit, dt.datetime.now().isoformat(timespec="seconds")),
        )


def user_has_tenant_access(user: dict[str, object] | None, tenant_key: str) -> bool:
    if not user:
        return False
    if user.get("role") in {"admin", "hospital_admin", "auditor"}:
        return True
    return str(user.get("tenant_key") or "") == tenant_key


def tenant_projection_scope(tenant_key: str) -> str:
    return f"tenant:{tenant_key}"


def fetch_tenant_summary(tenant_key: str) -> dict[str, object]:
    with get_connection() as connection:
        appointments = connection.execute("SELECT COUNT(*) FROM appointments WHERE tenant_key = ?", (tenant_key,)).fetchone()[0]
        workflows = connection.execute("SELECT COUNT(DISTINCT workflow_id) FROM workflow_events WHERE tenant_key = ?", (tenant_key,)).fetchone()[0]
        members = connection.execute("SELECT COUNT(*) FROM tenant_memberships WHERE tenant_key = ?", (tenant_key,)).fetchone()[0]
    return {"tenant_key": tenant_key, "appointment_count": int(appointments or 0), "workflow_count": int(workflows or 0), "membership_count": int(members or 0)}
