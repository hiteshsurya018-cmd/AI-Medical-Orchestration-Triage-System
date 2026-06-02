from __future__ import annotations

from ..contracts import ToolExecutionTelemetry
from .base import BaseRepository, ReplayTransactionContext


class TelemetryRepository(BaseRepository):
    transaction_context = ReplayTransactionContext

    def record_tool_execution(self, telemetry: ToolExecutionTelemetry) -> None:
        with self.transaction_context() as connection:
            existing = connection.execute(
                "SELECT id FROM tool_execution_logs WHERE invocation_id = :invocation_id",
                {"invocation_id": telemetry.invocation_id},
            ).fetchone()
            if existing is not None:
                self.increment_metric("docq_duplicate_tool_execution_prevented_total")
                return
            connection.execute(
                """
                INSERT INTO tool_execution_logs (
                    invocation_id, workflow_id, trace_id, tool_name, agent, parent_event_id, replay_branch_id,
                    latency_ms, success, fallback_used, error, created_at
                ) VALUES (
                    :invocation_id, :workflow_id, :trace_id, :tool_name, :agent, :parent_event_id, :replay_branch_id,
                    :latency_ms, :success, :fallback_used, :error, :created_at
                )
                """,
                {
                    "invocation_id": telemetry.invocation_id,
                    "workflow_id": telemetry.workflow_id,
                    "trace_id": telemetry.trace_id,
                    "tool_name": telemetry.tool_name,
                    "agent": telemetry.agent,
                    "parent_event_id": telemetry.parent_event_id,
                    "replay_branch_id": telemetry.replay_branch_id,
                    "latency_ms": telemetry.latency_ms,
                    "success": 1 if telemetry.success else 0,
                    "fallback_used": 1 if telemetry.fallback_used else 0,
                    "error": telemetry.error,
                    "created_at": telemetry.created_at,
                },
            )

    def fetch_tool_execution_logs(self, limit: int = 200):
        return self.fetchall(
            """
            SELECT invocation_id, workflow_id, trace_id, tool_name, agent, latency_ms, success, fallback_used, error, created_at,
                   parent_event_id, replay_branch_id
            FROM tool_execution_logs
            ORDER BY created_at DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )
