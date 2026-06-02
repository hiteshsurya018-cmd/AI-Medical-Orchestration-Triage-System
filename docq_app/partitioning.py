from __future__ import annotations

import datetime as dt

from .db import get_connection


def determine_partition_key(created_at: str | None = None) -> str:
    timestamp = dt.datetime.fromisoformat(created_at) if created_at else dt.datetime.now()
    return timestamp.strftime("%Y_%m")


def fetch_partition_metadata(table_name: str) -> dict[str, object] | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM partition_metadata WHERE table_name = ?",
            (table_name,),
        ).fetchone()
    return dict(row) if row is not None else None


def list_partition_metadata() -> list[dict[str, object]]:
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM partition_metadata ORDER BY table_name ASC").fetchall()
    return [dict(row) for row in rows]


def build_partition_route(table_name: str, *, created_at: str | None = None) -> dict[str, object]:
    metadata = fetch_partition_metadata(table_name) or {
        "table_name": table_name,
        "partition_strategy": "monthly",
        "retention_days": 90,
        "archive_enabled": 1,
    }
    partition_key = determine_partition_key(created_at)
    return {
        "table_name": table_name,
        "partition_key": partition_key,
        "partition_strategy": metadata["partition_strategy"],
        "retention_days": metadata["retention_days"],
        "archive_enabled": bool(metadata["archive_enabled"]),
    }
