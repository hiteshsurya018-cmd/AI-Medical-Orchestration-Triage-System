from __future__ import annotations

import datetime as dt
import json

from .db import get_connection
from .ml_governance import hash_payload


def export_recovery_bundle(*, tenant_key: str) -> dict[str, object]:
    with get_connection() as connection:
        snapshots = connection.execute(
            "SELECT * FROM replay_snapshots WHERE workflow_id LIKE ? ORDER BY created_at DESC LIMIT 200",
            (f"%{tenant_key}%",),
        ).fetchall()
        projections = connection.execute(
            "SELECT * FROM projection_snapshots ORDER BY updated_at DESC LIMIT 200",
        ).fetchall()
        payload = {
            "tenant_key": tenant_key,
            "snapshots": [dict(row) for row in snapshots],
            "projections": [dict(row) for row in projections],
        }
        checksum = hash_payload(payload)
        created_at = dt.datetime.now().isoformat(timespec="seconds")
        cursor = connection.execute(
            """
            INSERT INTO backup_exports (tenant_key, backup_type, checksum, payload_json, verified, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tenant_key, "recovery_bundle", checksum, json.dumps(payload, sort_keys=True), 1, created_at),
        )
    return {"id": int(cursor.lastrowid), "tenant_key": tenant_key, "checksum": checksum, "created_at": created_at}


def verify_recovery_bundle(export_id: int) -> dict[str, object]:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM backup_exports WHERE id = ?", (export_id,)).fetchone()
    if row is None:
        raise LookupError(f"backup export {export_id} not found")
    payload = json.loads(row["payload_json"] or "{}")
    checksum = hash_payload(payload)
    return {"export_id": export_id, "verified": checksum == row["checksum"], "checksum": checksum}
