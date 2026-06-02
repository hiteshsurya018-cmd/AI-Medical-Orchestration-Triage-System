from __future__ import annotations

import base64
import datetime as dt
import json
from itertools import cycle

from flask import current_app, g, has_app_context

from .db import get_connection
from .ml_governance import hash_payload


def _key_bytes() -> bytes:
    if has_app_context():
        return str(current_app.config.get("PII_ENCRYPTION_KEY", "docq-dev-pii-key")).encode("utf-8")
    return b"docq-dev-pii-key"


def encrypt_sensitive_value(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.encode("utf-8")
    encoded = bytes(a ^ b for a, b in zip(raw, cycle(_key_bytes())))
    return base64.urlsafe_b64encode(encoded).decode("ascii")


def decrypt_sensitive_value(value: str | None) -> str | None:
    if not value:
        return None
    raw = base64.urlsafe_b64decode(value.encode("ascii"))
    decoded = bytes(a ^ b for a, b in zip(raw, cycle(_key_bytes())))
    return decoded.decode("utf-8")


def mask_sensitive_value(value: str | None, *, keep: int = 2) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * max(len(value) - keep, 0)


def log_sensitive_access(
    *,
    tenant_key: str,
    access_type: str,
    resource_type: str,
    resource_id: str,
    masked_fields: dict[str, str] | None = None,
) -> None:
    actor = g.get("user") if has_app_context() else None
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO compliance_access_logs (
                actor_id, actor_email, tenant_key, access_type, resource_type, resource_id, masked_fields_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor.get("id") if actor else None,
                actor.get("email") if actor else "",
                tenant_key,
                access_type,
                resource_type,
                resource_id,
                json.dumps(masked_fields or {}, sort_keys=True),
                dt.datetime.now().isoformat(timespec="seconds"),
            ),
        )


def export_audit_bundle(tenant_key: str) -> dict[str, object]:
    with get_connection() as connection:
        access_logs = connection.execute(
            "SELECT * FROM compliance_access_logs WHERE tenant_key = ? ORDER BY created_at DESC, id DESC LIMIT 500",
            (tenant_key,),
        ).fetchall()
        workflow_events = connection.execute(
            "SELECT id, workflow_id, action, decision, created_at FROM workflow_events WHERE tenant_key = ? ORDER BY id DESC LIMIT 500",
            (tenant_key,),
        ).fetchall()
        payload = {
            "tenant_key": tenant_key,
            "access_logs": [dict(row) for row in access_logs],
            "workflow_events": [dict(row) for row in workflow_events],
        }
        checksum = hash_payload(payload)
        created_at = dt.datetime.now().isoformat(timespec="seconds")
        cursor = connection.execute(
            """
            INSERT INTO audit_exports (tenant_key, export_type, checksum, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tenant_key, "compliance_audit", checksum, json.dumps(payload, sort_keys=True), created_at),
        )
    return {"id": int(cursor.lastrowid), "tenant_key": tenant_key, "checksum": checksum, "payload": payload, "created_at": created_at}
