from __future__ import annotations

import datetime as dt
import json
from typing import Any

from .base import BaseRepository, GovernanceTransactionContext, RepositoryOperationResult


class WorkerExecutionRepository(BaseRepository):
    transaction_context = GovernanceTransactionContext

    def record_execution(
        self,
        *,
        task_id: str,
        task_name: str,
        workflow_id: str,
        originating_event_id: int | None,
        idempotency_key: str,
        execution_checksum: str,
        retry_count: int = 0,
        execution_generation: int = 0,
        owner_worker_id: str | None = None,
        lease_token: str | None = None,
        status: str = "started",
        payload: dict[str, Any] | None = None,
    ) -> RepositoryOperationResult:
        created_at = dt.datetime.now().isoformat(timespec="seconds")
        with self.transaction_context() as connection:
            existing = connection.execute(
                "SELECT id FROM worker_execution_ledger WHERE idempotency_key = :idempotency_key",
                {"idempotency_key": idempotency_key},
            ).fetchone()
            if existing is not None:
                self.increment_metric("docq_worker_idempotency_collision_total")
                return RepositoryOperationResult(created=False, identity=int(existing["id"]))
            cursor = connection.execute(
                """
                INSERT INTO worker_execution_ledger (
                    task_id, task_name, workflow_id, originating_event_id, idempotency_key,
                    execution_checksum, retry_count, execution_generation, owner_worker_id, lease_token,
                    execution_state, created_at, updated_at, payload_json
                ) VALUES (:task_id, :task_name, :workflow_id, :originating_event_id, :idempotency_key,
                          :execution_checksum, :retry_count, :execution_generation, :owner_worker_id, :lease_token,
                          :execution_state, :created_at, :updated_at, :payload_json)
                """,
                {
                    "task_id": task_id,
                    "task_name": task_name,
                    "workflow_id": workflow_id,
                    "originating_event_id": originating_event_id,
                    "idempotency_key": idempotency_key,
                    "execution_checksum": execution_checksum,
                    "retry_count": retry_count,
                    "execution_generation": execution_generation,
                    "owner_worker_id": owner_worker_id,
                    "lease_token": lease_token,
                    "execution_state": status,
                    "created_at": created_at,
                    "updated_at": created_at,
                    "payload_json": json.dumps(payload or {}, sort_keys=True),
                },
            )
            return RepositoryOperationResult(created=True, identity=int(cursor.lastrowid))

    def mark_execution_state(self, *, task_id: str, status: str) -> None:
        with self.transaction_context() as connection:
            connection.execute(
                """
                UPDATE worker_execution_ledger
                SET execution_state = :execution_state, updated_at = :updated_at
                WHERE task_id = :task_id
                """,
                {"task_id": task_id, "execution_state": status, "updated_at": dt.datetime.now().isoformat(timespec="seconds")},
            )

    def fetch_execution_by_key(self, *, idempotency_key: str):
        return self.fetchone(
            "SELECT * FROM worker_execution_ledger WHERE idempotency_key = :idempotency_key",
            {"idempotency_key": idempotency_key},
        )
