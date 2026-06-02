from __future__ import annotations

import datetime as dt

from .advisory_locks import acquire_advisory_lock, release_advisory_lock
from .appointments import build_workflow_replay_diff, record_governance_event
from .intelligence_rollups import build_operational_rollup
from .ml_governance import hash_payload
from .observability import metrics_registry
from .replay_snapshots import hydrate_workflow_replay


def run_distributed_replay_hydration(
    workflow_id: str,
    *,
    worker_id: str,
    limit: int = 200,
) -> dict[str, object]:
    lock = acquire_advisory_lock(lock_key=f"replay-hydration:{workflow_id}", owner_id=worker_id, timeout_seconds=180)
    if not lock.acquired:
        return {"workflow_id": workflow_id, "hydrated": False, "reason": lock.detail}
    try:
        started_at = dt.datetime.now()
        hydration = hydrate_workflow_replay(workflow_id, limit=limit)
        elapsed_ms = (dt.datetime.now() - started_at).total_seconds() * 1000.0
        metrics_registry.set_gauge("docq_replay_worker_throughput_latency_ms", round(elapsed_ms, 2))
        checksum = hydration.checkpoint.checkpoint_checksum if hydration.checkpoint else hash_payload({"workflow_id": workflow_id, "limit": limit})
        record_governance_event(
            f"replay-worker:{workflow_id}:{worker_id}",
            action="distributed_replay_hydrated",
            decision="accepted",
            payload={
                "workflow_id": workflow_id,
                "snapshot_id": hydration.snapshot.id if hydration.snapshot else None,
                "hydration_generation": hydration.checkpoint.hydration_generation if hydration.checkpoint else 0,
                "lease_token": lock.lock_token,
                "replay_checkpoint_id": hydration.checkpoint.snapshot_id if hydration.checkpoint else None,
                "governance_checksum": checksum,
            },
            confidence=100.0,
        )
        return {
            "workflow_id": workflow_id,
            "hydrated": True,
            "snapshot_hit": hydration.snapshot_hit,
            "step_count": hydration.replay.step_count,
            "checkpoint_checksum": checksum,
        }
    finally:
        release_advisory_lock(lock_key=f"replay-hydration:{workflow_id}", owner_id=worker_id)


def run_replay_diff_worker(
    workflow_a: str,
    workflow_b: str,
    *,
    worker_id: str,
    limit: int = 200,
) -> dict[str, object]:
    pair_key = f"{workflow_a}:{workflow_b}"
    lock = acquire_advisory_lock(lock_key=f"replay-diff:{pair_key}", owner_id=worker_id, timeout_seconds=180)
    if not lock.acquired:
        return {"workflow_pair": pair_key, "completed": False, "reason": lock.detail}
    try:
        diff = build_workflow_replay_diff(workflow_a, workflow_b, limit=limit)
        checksum = hash_payload({"workflow_a": workflow_a, "workflow_b": workflow_b, "summary": diff.summary})
        record_governance_event(
            f"replay-diff:{pair_key}:{worker_id}",
            action="distributed_replay_diff_completed",
            decision="accepted",
            payload={
                "workflow_a": workflow_a,
                "workflow_b": workflow_b,
                "divergence_point": diff.divergence_point,
                "governance_checksum": checksum,
                "lease_token": lock.lock_token,
            },
            confidence=100.0,
        )
        return {"workflow_pair": pair_key, "completed": True, "checksum": checksum, "divergence_point": diff.divergence_point}
    finally:
        release_advisory_lock(lock_key=f"replay-diff:{pair_key}", owner_id=worker_id)


def run_rollup_rebuild_worker(*, worker_id: str) -> dict[str, object]:
    lock = acquire_advisory_lock(lock_key="rollup:operational", owner_id=worker_id, timeout_seconds=180)
    if not lock.acquired:
        return {"rollup": "operational", "rebuilt": False, "reason": lock.detail}
    try:
        rollup = build_operational_rollup()
        return {"rollup": "operational", "rebuilt": True, "checksum": rollup["rollup_checksum"]}
    finally:
        release_advisory_lock(lock_key="rollup:operational", owner_id=worker_id)
