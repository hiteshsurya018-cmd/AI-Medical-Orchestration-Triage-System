from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from flask import current_app, has_app_context

from .ml_governance import hash_payload
from .observability import metrics_registry
from .production_db import create_sqlalchemy_engine

try:  # pragma: no cover - optional dependency path
    from sqlalchemy import text
except Exception:  # pragma: no cover
    text = None


@dataclass(frozen=True)
class AdvisoryLockResult:
    acquired: bool
    lock_key: str
    owner_id: str
    lock_token: str = ""
    detail: str = ""


def _database_url() -> str:
    if has_app_context():
        return str(current_app.config.get("DATABASE_URL", ""))
    return ""


def _is_postgres() -> bool:
    return _database_url().startswith("postgres")


def _lock_token(lock_key: str, owner_id: str) -> str:
    return hash_payload({"lock_key": lock_key, "owner_id": owner_id})[:32]


def acquire_advisory_lock(*, lock_key: str, owner_id: str, timeout_seconds: int = 60) -> AdvisoryLockResult:
    if _is_postgres() and text is not None:
        return _acquire_postgres_lock(lock_key=lock_key, owner_id=owner_id)
    return _acquire_table_lock(lock_key=lock_key, owner_id=owner_id, timeout_seconds=timeout_seconds)


def release_advisory_lock(*, lock_key: str, owner_id: str) -> AdvisoryLockResult:
    if _is_postgres() and text is not None:
        return _release_postgres_lock(lock_key=lock_key, owner_id=owner_id)
    return _release_table_lock(lock_key=lock_key, owner_id=owner_id)


def renew_advisory_lock(*, lock_key: str, owner_id: str, timeout_seconds: int = 60) -> AdvisoryLockResult:
    if _is_postgres() and text is not None:
        return _acquire_postgres_lock(lock_key=lock_key, owner_id=owner_id)
    return _renew_table_lock(lock_key=lock_key, owner_id=owner_id, timeout_seconds=timeout_seconds)


def _acquire_postgres_lock(*, lock_key: str, owner_id: str) -> AdvisoryLockResult:
    engine = create_sqlalchemy_engine(_database_url())
    token = _lock_token(lock_key, owner_id)
    digest = int(hash_payload({"lock_key": lock_key})[:15], 16)
    try:
        with engine.begin() as connection:
            acquired = bool(connection.execute(text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": digest}).scalar())
        if acquired:
            metrics_registry.increment("docq_advisory_lock_acquired_total")
            return AdvisoryLockResult(acquired=True, lock_key=lock_key, owner_id=owner_id, lock_token=token, detail="postgres advisory lock acquired")
        metrics_registry.increment("docq_advisory_lock_contention_total")
        return AdvisoryLockResult(acquired=False, lock_key=lock_key, owner_id=owner_id, detail="postgres advisory lock busy")
    finally:
        engine.dispose()


def _release_postgres_lock(*, lock_key: str, owner_id: str) -> AdvisoryLockResult:
    engine = create_sqlalchemy_engine(_database_url())
    digest = int(hash_payload({"lock_key": lock_key})[:15], 16)
    token = _lock_token(lock_key, owner_id)
    try:
        with engine.begin() as connection:
            released = bool(connection.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": digest}).scalar())
        return AdvisoryLockResult(acquired=released, lock_key=lock_key, owner_id=owner_id, lock_token=token, detail="postgres advisory lock released" if released else "postgres advisory lock not held")
    finally:
        engine.dispose()


def _acquire_table_lock(*, lock_key: str, owner_id: str, timeout_seconds: int) -> AdvisoryLockResult:
    from .db import transaction_scope

    now = dt.datetime.now()
    expires = (now + dt.timedelta(seconds=timeout_seconds)).isoformat(timespec="seconds")
    token = _lock_token(lock_key, owner_id)
    with transaction_scope() as connection:
        row = connection.execute(
            "SELECT * FROM advisory_locks WHERE lock_key = ? ORDER BY id DESC LIMIT 1",
            (lock_key,),
        ).fetchone()
        if row is not None and str(row["expires_at"] or "") > now.isoformat(timespec="seconds") and str(row["owner_id"]) != owner_id:
            metrics_registry.increment("docq_advisory_lock_contention_total")
            return AdvisoryLockResult(acquired=False, lock_key=lock_key, owner_id=owner_id, detail="advisory lock busy")
        if row is None:
            connection.execute(
                """
                INSERT INTO advisory_locks (lock_key, owner_id, lock_token, acquired_at, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (lock_key, owner_id, token, now.isoformat(timespec="seconds"), expires, now.isoformat(timespec="seconds")),
            )
        else:
            connection.execute(
                """
                UPDATE advisory_locks
                SET owner_id = ?, lock_token = ?, expires_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (owner_id, token, expires, now.isoformat(timespec="seconds"), int(row["id"])),
            )
    metrics_registry.increment("docq_advisory_lock_acquired_total")
    return AdvisoryLockResult(acquired=True, lock_key=lock_key, owner_id=owner_id, lock_token=token, detail="table advisory lock acquired")


def _renew_table_lock(*, lock_key: str, owner_id: str, timeout_seconds: int) -> AdvisoryLockResult:
    return _acquire_table_lock(lock_key=lock_key, owner_id=owner_id, timeout_seconds=timeout_seconds)


def _release_table_lock(*, lock_key: str, owner_id: str) -> AdvisoryLockResult:
    from .db import transaction_scope

    now = dt.datetime.now().isoformat(timespec="seconds")
    token = _lock_token(lock_key, owner_id)
    with transaction_scope() as connection:
        row = connection.execute(
            "SELECT * FROM advisory_locks WHERE lock_key = ? AND owner_id = ? ORDER BY id DESC LIMIT 1",
            (lock_key, owner_id),
        ).fetchone()
        if row is None:
            return AdvisoryLockResult(acquired=False, lock_key=lock_key, owner_id=owner_id, lock_token=token, detail="advisory lock not held")
        connection.execute(
            "UPDATE advisory_locks SET expires_at = ?, updated_at = ? WHERE id = ?",
            (now, now, int(row["id"])),
        )
    return AdvisoryLockResult(acquired=True, lock_key=lock_key, owner_id=owner_id, lock_token=token, detail="table advisory lock released")
