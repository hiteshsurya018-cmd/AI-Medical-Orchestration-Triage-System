from __future__ import annotations

import statistics
import time

from ..appointments import build_workflow_replay
from ..dashboard import build_dashboard_metrics
from ..governance_runtime import run_continuous_governance
from ..projection_workers import rebuild_projection
from ..workflow_engine import CaseWorkflowEngine


def _latency_summary(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "count": float(len(samples)),
        "min_ms": round(min(ordered), 2) if ordered else 0.0,
        "avg_ms": round(statistics.mean(ordered), 2) if ordered else 0.0,
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2) if ordered else 0.0,
        "max_ms": round(max(ordered), 2) if ordered else 0.0,
    }


def run_benchmark_suite(config: dict[str, object], *, iterations: int = 3) -> dict[str, object]:
    engine = CaseWorkflowEngine()
    workflow_ids: list[str] = []
    orchestration_samples: list[float] = []
    replay_samples: list[float] = []
    projection_samples: list[float] = []
    governance_samples: list[float] = []

    for index in range(iterations):
        workflow_id = f"benchmark-workflow-{index}"
        start = time.perf_counter()
        engine.run_intake(
            conversation_id=workflow_id,
            raw_message="Persistent chest pain and fatigue",
            patient_id=None,
            patient_email="",
            patient_phone="",
            actor_role="public",
            profile=None,
            stored_age=61,
            stored_history="hypertension",
        )
        orchestration_samples.append((time.perf_counter() - start) * 1000.0)
        workflow_ids.append(workflow_id)

        start = time.perf_counter()
        build_workflow_replay(workflow_id, limit=120)
        replay_samples.append((time.perf_counter() - start) * 1000.0)

    for _ in range(max(1, iterations // 2)):
        start = time.perf_counter()
        rebuild_projection("workflow_projection", worker_id="benchmark-worker", batch_size=200)
        projection_samples.append((time.perf_counter() - start) * 1000.0)

        start = time.perf_counter()
        run_continuous_governance(refresh=True)
        governance_samples.append((time.perf_counter() - start) * 1000.0)

    dashboard_snapshot = build_dashboard_metrics(config)
    return {
        "iterations": iterations,
        "workflows": workflow_ids,
        "orchestration": _latency_summary(orchestration_samples),
        "replay": _latency_summary(replay_samples),
        "projection_rebuild": _latency_summary(projection_samples),
        "governance": _latency_summary(governance_samples),
        "active_workflows": dashboard_snapshot["workflow_metrics"]["active_workflows"],
    }
