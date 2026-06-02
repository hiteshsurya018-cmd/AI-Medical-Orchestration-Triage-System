from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Callable

from .contracts import EventCompatibilityResult, EventEnvelope, EventSchemaVersion
from .ml_governance import hash_payload
from .observability import metrics_registry
from .repositories import WorkflowEventRepository


class EventPublisher:
    def publish_pending(self, limit: int = 100) -> int:  # pragma: no cover - interface
        raise NotImplementedError

    def describe(self) -> dict[str, object]:
        return {"backend": self.__class__.__name__.lower()}


class EventConsumer:
    consumer_id: str

    def consume(self, envelope: EventEnvelope, *, outbox_id: int) -> None:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class EventSubscription:
    consumer_id: str
    handler: Callable[[EventEnvelope], None] | Callable[..., None]
    ordered: bool = True


class EventSubscriptionRegistry:
    def __init__(self) -> None:
        self._subscriptions: list[EventSubscription] = []

    def register(self, consumer_id: str, handler: Callable[..., None], *, ordered: bool = True) -> None:
        if any(item.consumer_id == consumer_id for item in self._subscriptions):
            return
        self._subscriptions.append(EventSubscription(consumer_id=consumer_id, handler=handler, ordered=ordered))

    def subscriptions(self) -> list[EventSubscription]:
        return list(self._subscriptions)


def validate_event_envelope(payload: dict[str, object]) -> EventCompatibilityResult:
    required = ["event_id", "event_type", "workflow_id", "trace_id", "payload_checksum", "created_at"]
    missing = [field for field in required if field not in payload or payload.get(field) in {None, ""}]
    compatible = not missing and str(payload.get("schema_version", "v1")) == EventSchemaVersion.V1.value
    return EventCompatibilityResult(
        schema_version=EventSchemaVersion.V1,
        compatible=compatible,
        detail="compatible" if compatible else "invalid envelope",
        missing_fields=missing,
    )


class InProcessEventBus(EventPublisher):
    def __init__(self, registry: EventSubscriptionRegistry | None = None) -> None:
        self.registry = registry or EventSubscriptionRegistry()
        self.workflow_repository = WorkflowEventRepository()

    def publish_pending(self, limit: int = 100) -> int:
        published = 0
        rows = self.workflow_repository.fetch_pending_outbox(limit=limit)
        metrics_registry.set_gauge("docq_event_outbox_backlog", float(len(rows)))
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            compatibility = validate_event_envelope(
                {
                    "schema_version": row["schema_version"],
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "workflow_id": row["workflow_id"],
                    "trace_id": row["trace_id"],
                    "payload_checksum": row["payload_checksum"],
                    "created_at": row["created_at"],
                }
            )
            if not compatibility.compatible:
                metrics_registry.increment("docq_event_schema_compatibility_failures_total")
                self.workflow_repository.mark_outbox_retry(int(row["id"]), generation=int(row["publish_generation"] or 0) + 1)
                continue
            replay_checksum = hash_payload(
                {
                    "workflow_id": row["workflow_id"],
                    "root_event_id": payload.get("root_event_id"),
                    "replay_branch_id": row["replay_branch_id"],
                    "payload_checksum": row["payload_checksum"],
                }
            )
            envelope = EventEnvelope(
                schema_version=EventSchemaVersion(str(row["schema_version"])),
                event_id=int(row["event_id"]),
                event_type=str(row["event_type"]),
                aggregate_id=str(row["aggregate_id"]),
                workflow_id=str(row["workflow_id"]),
                trace_id=str(row["trace_id"]),
                root_event_id=payload.get("root_event_id"),
                causation_id=row["causation_id"],
                replay_branch_id=str(row["replay_branch_id"]),
                replay_checksum=replay_checksum,
                payload_checksum=str(row["payload_checksum"]),
                governance_context=payload.get("governance_context", {}),
                evaluation_context=payload.get("evaluation_context", {}),
                worker_generation=int(payload.get("worker_generation", 0) or 0),
                snapshot_reference=payload.get("snapshot_reference"),
                payload=payload,
                created_at=str(row["created_at"]),
            )
            dispatch_started = dt.datetime.now()
            for subscription in self.registry.subscriptions():
                subscription.handler(envelope, outbox_id=int(row["id"]))
            elapsed_ms = (dt.datetime.now() - dispatch_started).total_seconds() * 1000.0
            metrics_registry.set_gauge("docq_event_publish_latency_ms", round(elapsed_ms, 2))
            self.workflow_repository.mark_outbox_published(int(row["id"]), generation=int(row["publish_generation"] or 0) + 1)
            published += 1
        return published


event_subscription_registry = EventSubscriptionRegistry()
event_publisher = InProcessEventBus(event_subscription_registry)


def bootstrap_default_consumers() -> None:
    from .event_consumers import (
        governance_trigger_consumer,
        intelligence_rollup_consumer,
        notification_dispatch_consumer,
        projection_consumer,
        telemetry_aggregation_consumer,
    )

    event_subscription_registry.register("projection_consumer", projection_consumer)
    event_subscription_registry.register("intelligence_rollup_consumer", intelligence_rollup_consumer)
    event_subscription_registry.register("notification_dispatch_consumer", notification_dispatch_consumer)
    event_subscription_registry.register("governance_trigger_consumer", governance_trigger_consumer)
    event_subscription_registry.register("telemetry_aggregation_consumer", telemetry_aggregation_consumer)


def configure_event_publisher(*, backend: str, nats_url: str, node_id: str) -> EventPublisher:
    global event_publisher
    normalized = (backend or "inprocess").strip().lower()
    if normalized == "nats":
        try:
            from .event_bus_nats import NatsJetStreamEventBus

            event_publisher = NatsJetStreamEventBus(
                registry=event_subscription_registry,
                nats_url=nats_url,
                node_id=node_id,
            )
        except Exception:
            event_publisher = InProcessEventBus(event_subscription_registry)
    else:
        event_publisher = InProcessEventBus(event_subscription_registry)
    return event_publisher


def get_event_publisher() -> EventPublisher:
    return event_publisher
