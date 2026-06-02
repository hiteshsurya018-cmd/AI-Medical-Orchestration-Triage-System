from __future__ import annotations

import datetime as dt
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from ..db import get_connection, transaction_scope
from ..observability import metrics_registry


@dataclass
class RepositoryOperationResult:
    created: bool
    identity: int | str | None = None


class ReplayTransactionContext(AbstractContextManager):
    def __enter__(self):
        self._scope = transaction_scope()
        self.connection = self._scope.__enter__()
        self.started_at = dt.datetime.now()
        return self.connection

    def __exit__(self, exc_type, exc, tb):
        elapsed_ms = (dt.datetime.now() - self.started_at).total_seconds() * 1000.0
        metrics_registry.set_gauge("docq_repository_replay_transaction_latency_ms", round(elapsed_ms, 2))
        if exc_type is not None:
            metrics_registry.increment("docq_repository_replay_transaction_rollbacks_total")
        return self._scope.__exit__(exc_type, exc, tb)


class GovernanceTransactionContext(AbstractContextManager):
    def __enter__(self):
        self._scope = transaction_scope()
        self.connection = self._scope.__enter__()
        self.started_at = dt.datetime.now()
        return self.connection

    def __exit__(self, exc_type, exc, tb):
        elapsed_ms = (dt.datetime.now() - self.started_at).total_seconds() * 1000.0
        metrics_registry.set_gauge("docq_repository_governance_transaction_latency_ms", round(elapsed_ms, 2))
        if exc_type is not None:
            metrics_registry.increment("docq_repository_governance_transaction_rollbacks_total")
        return self._scope.__exit__(exc_type, exc, tb)


class EvaluationTransactionContext(AbstractContextManager):
    def __enter__(self):
        self._scope = transaction_scope()
        self.connection = self._scope.__enter__()
        self.started_at = dt.datetime.now()
        return self.connection

    def __exit__(self, exc_type, exc, tb):
        elapsed_ms = (dt.datetime.now() - self.started_at).total_seconds() * 1000.0
        metrics_registry.set_gauge("docq_repository_evaluation_transaction_latency_ms", round(elapsed_ms, 2))
        if exc_type is not None:
            metrics_registry.increment("docq_repository_evaluation_transaction_rollbacks_total")
        return self._scope.__exit__(exc_type, exc, tb)


class BaseRepository:
    transaction_context = ReplayTransactionContext

    def connection(self):
        return get_connection()

    def fetchall(self, query: str, params: dict[str, Any] | tuple[Any, ...] | None = None):
        started_at = dt.datetime.now()
        connection = self.connection()
        try:
            rows = connection.execute(query, params or ()).fetchall()
            self._record_latency("fetchall", started_at)
            return rows
        finally:
            connection.close()

    def fetchone(self, query: str, params: dict[str, Any] | tuple[Any, ...] | None = None):
        started_at = dt.datetime.now()
        connection = self.connection()
        try:
            row = connection.execute(query, params or ()).fetchone()
            self._record_latency("fetchone", started_at)
            return row
        finally:
            connection.close()

    def execute_write(self, query: str, params: dict[str, Any] | tuple[Any, ...] | None = None) -> None:
        started_at = dt.datetime.now()
        with self.transaction_context() as connection:
            connection.execute(query, params or ())
        self._record_latency("write", started_at)

    def increment_metric(self, key: str, value: float = 1.0) -> None:
        metrics_registry.increment(key, value)

    def set_metric(self, key: str, value: float) -> None:
        metrics_registry.set_gauge(key, value)

    def _record_latency(self, operation: str, started_at: dt.datetime) -> None:
        elapsed_ms = (dt.datetime.now() - started_at).total_seconds() * 1000.0
        metrics_registry.set_gauge(
            f"docq_repository_{self.__class__.__name__.lower()}_{operation}_latency_ms",
            round(elapsed_ms, 2),
        )
