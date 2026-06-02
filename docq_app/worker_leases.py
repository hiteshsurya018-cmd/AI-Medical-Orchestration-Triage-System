from __future__ import annotations

import datetime as dt

from .contracts import WorkerLease, WorkerLeaseResult
from .db import transaction_scope
from .ml_governance import hash_payload
from .observability import metrics_registry


def _build_lease_contract(row) -> WorkerLease:
    return WorkerLease(
        id=int(row["id"]),
        worker_id=str(row["worker_id"]),
        task_id=str(row["task_id"]),
        workflow_id=str(row["workflow_id"]),
        lease_token=str(row["lease_token"]),
        lease_expiration=str(row["lease_expiration"]),
        retry_generation=int(row["retry_generation"] or 0),
        execution_checksum=str(row["execution_checksum"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
    )


def acquire_worker_lease(
    *,
    worker_id: str,
    task_id: str,
    workflow_id: str,
    retry_generation: int,
    execution_checksum: str,
    lease_seconds: int = 120,
) -> WorkerLeaseResult:
    now = dt.datetime.now()
    expires = (now + dt.timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
    lease_token = hash_payload(
        {
            "worker_id": worker_id,
            "task_id": task_id,
            "workflow_id": workflow_id,
            "retry_generation": retry_generation,
            "execution_checksum": execution_checksum,
        }
    )[:24]
    with transaction_scope() as connection:
        existing = connection.execute(
            "SELECT * FROM worker_leases WHERE task_id = ? ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if existing is not None and str(existing["lease_expiration"]) > now.isoformat(timespec="seconds"):
            metrics_registry.increment("docq_worker_lease_contention_total")
            return WorkerLeaseResult(acquired=False, lease=_build_lease_contract(existing), reason="lease already active")
        if existing is None:
            cursor = connection.execute(
                """
                INSERT INTO worker_leases (
                    worker_id, task_id, workflow_id, lease_token, lease_expiration,
                    retry_generation, execution_checksum, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    worker_id,
                    task_id,
                    workflow_id,
                    lease_token,
                    expires,
                    retry_generation,
                    execution_checksum,
                    now.isoformat(timespec="seconds"),
                    now.isoformat(timespec="seconds"),
                ),
            )
            row = connection.execute("SELECT * FROM worker_leases WHERE id = ?", (int(cursor.lastrowid),)).fetchone()
        else:
            connection.execute(
                """
                UPDATE worker_leases
                SET worker_id = ?, workflow_id = ?, lease_token = ?, lease_expiration = ?,
                    retry_generation = ?, execution_checksum = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    worker_id,
                    workflow_id,
                    lease_token,
                    expires,
                    retry_generation,
                    execution_checksum,
                    now.isoformat(timespec="seconds"),
                    int(existing["id"]),
                ),
            )
            row = connection.execute("SELECT * FROM worker_leases WHERE id = ?", (int(existing["id"]),)).fetchone()
    result = WorkerLeaseResult(acquired=True, lease=_build_lease_contract(row), reason="lease acquired")
    metrics_registry.increment("docq_worker_lease_acquired_total")
    from .appointments import record_governance_event

    record_governance_event(
        f"lease-{task_id}",
        action="worker_lease_acquired",
        decision="accepted",
        payload={
            "task_id": task_id,
            "workflow_id": workflow_id,
            "lease_token": result.lease.lease_token if result.lease else "",
            "worker_generation": retry_generation,
            "execution_checksum": execution_checksum,
        },
        confidence=100.0,
    )
    return result


def renew_worker_lease(lease_token: str, *, lease_seconds: int = 120) -> WorkerLeaseResult:
    now = dt.datetime.now()
    expires = (now + dt.timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
    with transaction_scope() as connection:
        existing = connection.execute("SELECT * FROM worker_leases WHERE lease_token = ? LIMIT 1", (lease_token,)).fetchone()
        if existing is None:
            return WorkerLeaseResult(acquired=False, lease=None, reason="lease not found")
        connection.execute(
            "UPDATE worker_leases SET lease_expiration = ?, updated_at = ? WHERE id = ?",
            (expires, now.isoformat(timespec="seconds"), int(existing["id"])),
        )
        row = connection.execute("SELECT * FROM worker_leases WHERE id = ?", (int(existing["id"]),)).fetchone()
    result = WorkerLeaseResult(acquired=True, lease=_build_lease_contract(row), reason="lease renewed")
    metrics_registry.increment("docq_worker_lease_renewed_total")
    from .appointments import record_governance_event

    record_governance_event(
        f"lease-renew-{result.lease.task_id if result.lease else lease_token}",
        action="worker_lease_renewed",
        decision="accepted",
        payload={
            "task_id": result.lease.task_id if result.lease else "",
            "workflow_id": result.lease.workflow_id if result.lease else "",
            "lease_token": lease_token,
            "worker_generation": result.lease.retry_generation if result.lease else 0,
        },
        confidence=100.0,
    )
    return result


def release_worker_lease(lease_token: str) -> WorkerLeaseResult:
    now = dt.datetime.now().isoformat(timespec="seconds")
    with transaction_scope() as connection:
        existing = connection.execute("SELECT * FROM worker_leases WHERE lease_token = ? LIMIT 1", (lease_token,)).fetchone()
        if existing is None:
            return WorkerLeaseResult(acquired=False, lease=None, reason="lease not found")
        connection.execute("UPDATE worker_leases SET lease_expiration = ?, updated_at = ? WHERE id = ?", (now, now, int(existing["id"])))
        row = connection.execute("SELECT * FROM worker_leases WHERE id = ?", (int(existing["id"]),)).fetchone()
    result = WorkerLeaseResult(acquired=True, lease=_build_lease_contract(row), reason="lease released")
    metrics_registry.increment("docq_worker_lease_released_total")
    from .appointments import record_governance_event

    record_governance_event(
        f"lease-release-{result.lease.task_id if result.lease else lease_token}",
        action="worker_lease_released",
        decision="accepted",
        payload={
            "task_id": result.lease.task_id if result.lease else "",
            "workflow_id": result.lease.workflow_id if result.lease else "",
            "lease_token": lease_token,
            "worker_generation": result.lease.retry_generation if result.lease else 0,
        },
        confidence=100.0,
    )
    return result
