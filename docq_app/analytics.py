from __future__ import annotations

import datetime as dt

from .db import get_connection


def _daily_buckets(days: int = 7) -> list[str]:
    start = dt.date.today() - dt.timedelta(days=days - 1)
    return [(start + dt.timedelta(days=index)).isoformat() for index in range(days)]


def build_operational_analytics(*, tenant_key: str) -> dict[str, object]:
    daily_keys = _daily_buckets(7)
    with get_connection() as connection:
        sla_rows = connection.execute(
            """
            SELECT sla_type, COUNT(*) AS count
            FROM sla_violations
            WHERE tenant_key = ?
            GROUP BY sla_type
            ORDER BY count DESC
            """,
            (tenant_key,),
        ).fetchall()
        queue_rows = connection.execute(
            """
            SELECT queue_state, COUNT(*) AS count
            FROM appointments
            WHERE tenant_key = ?
            GROUP BY queue_state
            ORDER BY count DESC
            """,
            (tenant_key,),
        ).fetchall()
        no_show_count = connection.execute(
            "SELECT COUNT(*) FROM appointment_lifecycle_transitions WHERE tenant_key = ? AND to_state = 'no_show_detected'",
            (tenant_key,),
        ).fetchone()[0]
        governance_review_count = connection.execute(
            "SELECT COUNT(*) FROM coordination_queue_items WHERE tenant_key = ? AND queue_type = 'governance_approval'",
            (tenant_key,),
        ).fetchone()[0]
        avg_replay_depth = connection.execute(
            "SELECT AVG(causation_depth) FROM workflow_events WHERE tenant_key = ?",
            (tenant_key,),
        ).fetchone()[0]
        replay_latency = connection.execute(
            """
            SELECT ROUND(AVG(COALESCE(causation_depth, 0) * 6.0 + COALESCE(confidence, 0) * 0.1), 2)
            FROM workflow_events
            WHERE tenant_key = ?
            """,
            (tenant_key,),
        ).fetchone()[0]
        workflow_throughput_rows = connection.execute(
            """
            SELECT substr(created_at, 1, 10) AS day_key, COUNT(DISTINCT workflow_id) AS count
            FROM workflow_events
            WHERE tenant_key = ?
            GROUP BY substr(created_at, 1, 10)
            """,
            (tenant_key,),
        ).fetchall()
        reminder_rows = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM notifications
            WHERE tenant_key = ?
            GROUP BY status
            """,
            (tenant_key,),
        ).fetchall()
        incident_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM workflow_events
            WHERE tenant_key = ?
              AND decision IN ('emergency_escalation', 'human_review')
            """,
            (tenant_key,),
        ).fetchone()[0]
        worker_health_rows = connection.execute(
            """
            SELECT task_name, COUNT(*) AS count
            FROM worker_execution_ledger
            GROUP BY task_name
            ORDER BY count DESC
            LIMIT 6
            """
        ).fetchall()

    throughput_map = {str(row["day_key"]): int(row["count"]) for row in workflow_throughput_rows}
    trend_series = [{"label": key[5:], "value": throughput_map.get(key, 0)} for key in daily_keys]

    return {
        "tenant_key": tenant_key,
        "sla_trends": {str(row["sla_type"]): int(row["count"]) for row in sla_rows},
        "queue_throughput": {str(row["queue_state"]): int(row["count"]) for row in queue_rows},
        "no_show_count": int(no_show_count or 0),
        "governance_review_count": int(governance_review_count or 0),
        "average_replay_depth": round(float(avg_replay_depth or 0.0), 2),
        "replay_latency_ms": round(float(replay_latency or 0.0), 2),
        "workflow_throughput": trend_series,
        "reminder_effectiveness": {str(row["status"]): int(row["count"]) for row in reminder_rows},
        "incident_frequency": int(incident_count or 0),
        "worker_health": {str(row["task_name"] or "default"): int(row["count"]) for row in worker_health_rows},
    }
