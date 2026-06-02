from __future__ import annotations

import os

from .ml_governance import hash_payload
from .repositories import WorkerExecutionRepository
from .worker_leases import acquire_worker_lease

try:
    from redis import Redis
    from rq import Queue, Worker
except Exception:  # pragma: no cover - optional runtime dependency
    Redis = None
    Queue = None
    Worker = None


worker_execution_repository = WorkerExecutionRepository()


def build_queue(redis_url: str, queue_name: str = "docq-default"):
    if Redis is None or Queue is None:
        return None
    redis_conn = Redis.from_url(redis_url)
    return Queue(queue_name, connection=redis_conn)


def enqueue_job(redis_url: str, task_path: str, *args, queue_name: str = "docq-default", **kwargs):
    queue = build_queue(redis_url, queue_name=queue_name)
    if queue is None:
        return None
    idempotency_key = kwargs.pop("idempotency_key", "")
    workflow_id = kwargs.pop("workflow_id", "")
    originating_event_id = kwargs.pop("originating_event_id", None)
    if idempotency_key:
        checksum = hash_payload({"task_path": task_path, "args": args, "kwargs": kwargs, "queue_name": queue_name})
        lease = acquire_worker_lease(
            worker_id="queue-scheduler",
            task_id=f"queued:{idempotency_key}",
            workflow_id=workflow_id or "worker-runtime",
            retry_generation=0,
            execution_checksum=checksum,
        )
        if not lease.acquired:
            return None
        ledger = worker_execution_repository.record_execution(
            task_id=f"queued:{idempotency_key}",
            task_name=task_path,
            workflow_id=workflow_id or "worker-runtime",
            originating_event_id=originating_event_id,
            idempotency_key=idempotency_key,
            execution_checksum=checksum,
            owner_worker_id="queue-scheduler",
            lease_token=lease.lease.lease_token if lease.lease else None,
            payload={"args": list(args), "kwargs": kwargs, "queue_name": queue_name},
        )
        if not ledger.created:
            return None
    return queue.enqueue(task_path, *args, **kwargs)


def start_worker(redis_url: str, queue_name: str = "docq-default") -> None:
    if Redis is None or Worker is None:
        raise RuntimeError("RQ worker dependencies are not installed.")
    redis_conn = Redis.from_url(redis_url)
    worker = Worker([queue_name], connection=redis_conn)
    worker.work()


if __name__ == "__main__":  # pragma: no cover
    start_worker(os.getenv("DOCQ_REDIS_URL", "redis://redis:6379/0"))
