from __future__ import annotations

import datetime as dt
import json

from ..db import get_connection


def record_integration_health(*, provider_key: str, tenant_key: str, status: str, detail: str = "", metadata: dict[str, object] | None = None) -> None:
    now = dt.datetime.now().isoformat(timespec="seconds")
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT id FROM integration_health WHERE provider_key = ? AND tenant_key = ?",
            (provider_key, tenant_key),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO integration_health (provider_key, tenant_key, status, last_error, last_checked_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (provider_key, tenant_key, status, detail, now, json.dumps(metadata or {}, sort_keys=True)),
            )
        else:
            connection.execute(
                """
                UPDATE integration_health
                SET status = ?, last_error = ?, last_checked_at = ?, metadata_json = ?
                WHERE id = ?
                """,
                (status, detail, now, json.dumps(metadata or {}, sort_keys=True), int(existing["id"])),
            )
