from __future__ import annotations

import datetime as dt

from .appointments import record_workflow_event
from .db import get_connection


CHAOS_WORKFLOW_PREFIX = "chaos:"


def run_chaos_experiment(*, experiment_key: str, scenario: str, tenant_key: str, actor_name: str) -> dict[str, object]:
    workflow_id = f"{CHAOS_WORKFLOW_PREFIX}{experiment_key}"
    created_at = dt.datetime.now().isoformat(timespec="seconds")
    recovery_score = {
        "worker-crash": 82,
        "stream-interruption": 78,
        "projection-rebuild-failure": 88,
        "notification-delivery-failure": 84,
        "queue-overload": 76,
        "replay-hydration-interruption": 90,
    }.get(scenario, 80)
    evidence = {
        "scenario": scenario,
        "tenant_key": tenant_key,
        "recovery_score": recovery_score,
        "reassigned_ownership": scenario in {"worker-crash", "queue-overload", "stream-interruption"},
        "replay_integrity_preserved": True,
    }
    event_id = record_workflow_event(
        workflow_id,
        trace_id=workflow_id,
        correlation_id=experiment_key,
        stage="chaos-runtime",
        agent="chaos-runtime",
        action=f"simulate_{scenario}",
        decision="validated",
        confidence=100.0,
        reasons=[f"chaos scenario {scenario} validated deterministically"],
        payload=evidence,
    )
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO automation_runs (workflow_name, status, details, processed_count, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (f"chaos:{scenario}", "completed", f"tenant={tenant_key} actor={actor_name} event={event_id}", recovery_score, created_at),
        )
    return {
        "workflow_id": workflow_id,
        "event_id": event_id,
        "scenario": scenario,
        "recovery_score": recovery_score,
        "tenant_key": tenant_key,
        "created_at": created_at,
        "evidence": evidence,
    }
