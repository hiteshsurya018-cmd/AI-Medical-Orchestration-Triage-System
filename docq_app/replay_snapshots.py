from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from .contracts import (
    HydrationLineageSummary,
    ReplayCheckpoint,
    ReplaySnapshot,
    SnapshotHydrationResult,
    SnapshotIntegrityCheck,
    WorkflowReplay,
    WorkflowReplayStep,
)
from .db import get_connection
from .observability import metrics_registry
from .pydantic_compat import model_dump
from .runtime_diagnostics import build_replay_checksum

SNAPSHOT_EVENT_INTERVAL = 5


def _canonical_snapshot_blob(blob: dict[str, Any]) -> str:
    return json.dumps(blob, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _snapshot_checksum(blob: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_snapshot_blob(blob).encode("utf-8")).hexdigest()


def _row_to_step(row) -> WorkflowReplayStep:
    from .appointments import build_workflow_event_record

    return WorkflowReplayStep(**model_dump(build_workflow_event_record(row)))


def fetch_latest_snapshot(workflow_id: str, replay_branch_id: str = "main"):
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT *
            FROM replay_snapshots
            WHERE workflow_id = ? AND replay_branch_id = ?
            ORDER BY snapshot_event_id DESC, id DESC
            LIMIT 1
            """,
            (workflow_id, replay_branch_id),
        ).fetchone()


def build_snapshot_contract(row) -> ReplaySnapshot:
    return ReplaySnapshot(
        id=int(row["id"]),
        workflow_id=str(row["workflow_id"]),
        replay_branch_id=str(row["replay_branch_id"] or "main"),
        snapshot_event_id=int(row["snapshot_event_id"]),
        snapshot_checksum=str(row["snapshot_checksum"]),
        workflow_state_blob=json.loads(row["workflow_state_blob"] or "{}"),
        lineage_metadata=json.loads(row["lineage_metadata"] or "{}"),
        created_at=str(row["created_at"]),
        invalidated_at=str(row["invalidated_at"]) if row["invalidated_at"] else None,
    )


def persist_replay_snapshot(replay: WorkflowReplay, replay_branch_id: str = "main") -> ReplaySnapshot | None:
    if replay.step_count < SNAPSHOT_EVENT_INTERVAL:
        return None
    snapshot_step_count = max((replay.step_count // SNAPSHOT_EVENT_INTERVAL) * SNAPSHOT_EVENT_INTERVAL, SNAPSHOT_EVENT_INTERVAL)
    snapshot_steps = replay.steps[:snapshot_step_count]
    snapshot_event_id = snapshot_steps[-1].event_id
    state_blob = {
        "steps": [model_dump(step) for step in snapshot_steps],
        "latest_decision": snapshot_steps[-1].decision if snapshot_steps else replay.latest_decision,
        "step_count": len(snapshot_steps),
    }
    lineage_metadata = {
        "root_event_id": snapshot_steps[0].root_event_id if snapshot_steps else None,
        "step_count": len(snapshot_steps),
        "latest_decision": state_blob["latest_decision"],
    }
    checksum = _snapshot_checksum(state_blob)
    now = dt.datetime.now().isoformat(timespec="seconds")
    with get_connection() as connection:
        existing = connection.execute(
            """
            SELECT *
            FROM replay_snapshots
            WHERE workflow_id = ? AND replay_branch_id = ? AND snapshot_event_id = ?
            LIMIT 1
            """,
            (replay.workflow_id, replay_branch_id, snapshot_event_id),
        ).fetchone()
        if existing is not None:
            return build_snapshot_contract(existing)
        cursor = connection.execute(
            """
            INSERT INTO replay_snapshots (
                workflow_id, replay_branch_id, snapshot_event_id, snapshot_checksum,
                workflow_state_blob, lineage_metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                replay.workflow_id,
                replay_branch_id,
                snapshot_event_id,
                checksum,
                json.dumps(state_blob, sort_keys=True),
                json.dumps(lineage_metadata, sort_keys=True),
                now,
            ),
        )
        row = connection.execute("SELECT * FROM replay_snapshots WHERE id = ?", (int(cursor.lastrowid),)).fetchone()
    snapshot = build_snapshot_contract(row) if row is not None else None
    if snapshot is not None:
        from .appointments import record_governance_event

        record_governance_event(
            f"snapshot-{replay.workflow_id}-{snapshot.id}",
            action="replay_snapshot_created",
            decision="accepted",
            payload={
                "workflow_id": replay.workflow_id,
                "snapshot_id": snapshot.id,
                "replay_checkpoint_id": snapshot.snapshot_event_id,
                "snapshot_checksum": snapshot.snapshot_checksum,
                "replay_branch_id": replay_branch_id,
            },
            confidence=100.0,
        )
    return snapshot


def fetch_events_after_checkpoint(workflow_id: str, after_event_id: int, limit: int = 200):
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT id, workflow_id, trace_id, correlation_id, causation_id, root_event_id,
                   parent_event_id, causation_depth, replay_branch_id,
                   stage, agent, action, decision, confidence, reasons, payload_json, created_at
            FROM workflow_events
            WHERE workflow_id = ? AND id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (workflow_id, after_event_id, limit),
        ).fetchall()


def fetch_full_events(workflow_id: str, limit: int = 200):
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT id, workflow_id, trace_id, correlation_id, causation_id, root_event_id,
                   parent_event_id, causation_depth, replay_branch_id,
                   stage, agent, action, decision, confidence, reasons, payload_json, created_at
            FROM workflow_events
            WHERE workflow_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (workflow_id, limit),
        ).fetchall()


def validate_snapshot(snapshot: ReplaySnapshot) -> SnapshotIntegrityCheck:
    actual = _snapshot_checksum(snapshot.workflow_state_blob)
    valid = actual == snapshot.snapshot_checksum and snapshot.invalidated_at is None
    return SnapshotIntegrityCheck(
        snapshot_id=snapshot.id,
        valid=valid,
        expected_checksum=snapshot.snapshot_checksum,
        actual_checksum=actual,
        detail="snapshot checksum valid" if valid else "snapshot checksum mismatch",
    )


def hydrate_workflow_replay(workflow_id: str, *, replay_branch_id: str = "main", limit: int = 200) -> SnapshotHydrationResult:
    started_at = dt.datetime.now()
    snapshot_row = fetch_latest_snapshot(workflow_id, replay_branch_id=replay_branch_id)
    snapshot = build_snapshot_contract(snapshot_row) if snapshot_row is not None else None
    steps: list[WorkflowReplayStep] = []
    snapshot_hit = False
    integrity = None
    hydration_generation = 0
    if snapshot is not None:
        integrity = validate_snapshot(snapshot)
        if integrity.valid:
            snapshot_hit = True
            hydration_generation = 1
            metrics_registry.increment("docq_replay_snapshot_hits_total")
            steps = [WorkflowReplayStep(**item) for item in snapshot.workflow_state_blob.get("steps", [])]
            rows = fetch_events_after_checkpoint(workflow_id, snapshot.snapshot_event_id, limit=max(limit, 1_000))
            steps.extend(_row_to_step(row) for row in rows)
        else:
            metrics_registry.increment("docq_replay_snapshot_invalid_total")
            snapshot = None
    if not snapshot_hit:
        rows = fetch_full_events(workflow_id, limit=max(limit, 1_000))
        steps = [_row_to_step(row) for row in rows]
    latest_decision = steps[-1].decision if steps else "unknown"
    replay = WorkflowReplay(workflow_id=workflow_id, step_count=len(steps), latest_decision=latest_decision, steps=steps)
    latest_snapshot = snapshot or persist_replay_snapshot(replay, replay_branch_id=replay_branch_id)
    if integrity is None and latest_snapshot is not None:
        integrity = validate_snapshot(latest_snapshot)
    checkpoint = ReplayCheckpoint(
        workflow_id=workflow_id,
        replay_branch_id=replay_branch_id,
        snapshot_id=latest_snapshot.id if latest_snapshot else None,
        checkpoint_event_id=steps[-1].event_id if steps else None,
        hydration_generation=hydration_generation,
        checkpoint_checksum=build_replay_checksum(replay).value,
    )
    lineage_summary = HydrationLineageSummary(
        workflow_id=workflow_id,
        replay_branch_id=replay_branch_id,
        root_event_id=steps[0].root_event_id if steps else None,
        snapshot_step_count=len(latest_snapshot.workflow_state_blob.get("steps", [])) if latest_snapshot else 0,
        incremental_step_count=max(len(steps) - (len(latest_snapshot.workflow_state_blob.get("steps", [])) if latest_snapshot else 0), 0),
        total_step_count=len(steps),
    )
    elapsed_ms = (dt.datetime.now() - started_at).total_seconds() * 1000.0
    metrics_registry.set_gauge("docq_replay_hydration_latency_ms", round(elapsed_ms, 2))
    metrics_registry.set_gauge("docq_replay_reconstruction_depth", float(lineage_summary.incremental_step_count))
    return SnapshotHydrationResult(
        workflow_id=workflow_id,
        snapshot_hit=snapshot_hit,
        replay=replay,
        snapshot=latest_snapshot,
        integrity=integrity,
        checkpoint=checkpoint,
        lineage_summary=lineage_summary,
    )
