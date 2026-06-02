from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from .contracts import EventCompatibilityResult, EventEnvelope, EventSchemaVersion
from .event_bus import EventPublisher, EventSubscriptionRegistry, validate_event_envelope
from .ml_governance import hash_payload
from .observability import metrics_registry
from .repositories import WorkflowEventRepository

try:  # pragma: no cover - optional runtime dependency
    from nats.js import api as js_api
except Exception:  # pragma: no cover
    js_api = None


STREAM_SUBJECTS = {
    "workflow": "workflow.events",
    "governance": "governance.events",
    "replay": "replay.events",
    "telemetry": "telemetry.events",
    "evaluation": "evaluation.events",
    "notification": "notification.events",
    "projection": "projection.events",
}


def resolve_event_subject(envelope: EventEnvelope) -> str:
    workflow_id = envelope.workflow_id
    event_type = envelope.event_type
    if workflow_id.startswith("ml-governance:"):
        return STREAM_SUBJECTS["governance"]
    if workflow_id.startswith("ml-eval:"):
        return STREAM_SUBJECTS["evaluation"]
    if workflow_id.startswith("security:"):
        return STREAM_SUBJECTS["governance"]
    if event_type in {"telemetry_aggregation", "tool_invoked"}:
        return STREAM_SUBJECTS["telemetry"]
    if event_type in {"replay_snapshot", "replay_hydration", "replay_diff"}:
        return STREAM_SUBJECTS["replay"]
    if event_type in {"notification_dispatch", "notification_retry"}:
        return STREAM_SUBJECTS["notification"]
    if event_type in {"projection_update", "projection_rebuild"}:
        return STREAM_SUBJECTS["projection"]
    return STREAM_SUBJECTS["workflow"]


@dataclass
class PublishedStreamRecord:
    subject: str
    sequence: int
    envelope: EventEnvelope
    published_at: str


class _MemoryJetStreamBackend:
    def __init__(self) -> None:
        self._records: list[PublishedStreamRecord] = []

    def publish(self, subject: str, envelope: EventEnvelope) -> PublishedStreamRecord:
        record = PublishedStreamRecord(
            subject=subject,
            sequence=len(self._records) + 1,
            envelope=envelope,
            published_at=dt.datetime.now().isoformat(timespec="seconds"),
        )
        self._records.append(record)
        return record

    def describe(self) -> dict[str, object]:
        return {"mode": "memory", "stream_count": len(self._records)}


class NatsJetStreamEventBus(EventPublisher):
    def __init__(
        self,
        registry: EventSubscriptionRegistry,
        *,
        nats_url: str,
        node_id: str,
    ) -> None:
        self.registry = registry
        self.nats_url = nats_url
        self.node_id = node_id
        self.workflow_repository = WorkflowEventRepository()
        self.backend = _MemoryJetStreamBackend()
        self._durable_prefix = f"docq-{node_id}"
        self._nats_capable = js_api is not None and not nats_url.startswith("memory://")

    def describe(self) -> dict[str, object]:
        details = self.backend.describe()
        details.update({"backend": "nats-jetstream", "nats_url": self.nats_url, "node_id": self.node_id, "capable": self._nats_capable})
        return details

    def publish_pending(self, limit: int = 100) -> int:
        rows = self.workflow_repository.fetch_pending_outbox(limit=limit)
        metrics_registry.set_gauge("docq_event_outbox_backlog", float(len(rows)))
        published = 0
        for row in rows:
            envelope = self._build_envelope(row)
            if envelope is None:
                self.workflow_repository.mark_outbox_retry(int(row["id"]), generation=int(row["publish_generation"] or 0) + 1)
                continue
            subject = resolve_event_subject(envelope)
            dispatch_started = dt.datetime.now()
            record = self.backend.publish(subject, envelope)
            for subscription in self.registry.subscriptions():
                subscription.handler(envelope, outbox_id=int(row["id"]))
            elapsed_ms = (dt.datetime.now() - dispatch_started).total_seconds() * 1000.0
            metrics_registry.set_gauge("docq_event_publish_latency_ms", round(elapsed_ms, 2))
            metrics_registry.set_gauge("docq_stream_delivery_latency_ms", round(elapsed_ms, 2))
            metrics_registry.set_gauge("docq_stream_sequence_latest", float(record.sequence))
            self.workflow_repository.mark_outbox_published(int(row["id"]), generation=int(row["publish_generation"] or 0) + 1)
            published += 1
        return published

    def _build_envelope(self, row) -> EventEnvelope | None:
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
            return None
        replay_checksum = hash_payload(
            {
                "workflow_id": row["workflow_id"],
                "root_event_id": payload.get("root_event_id"),
                "replay_branch_id": row["replay_branch_id"],
                "payload_checksum": row["payload_checksum"],
            }
        )
        payload.setdefault("stream_subject", resolve_event_subject_from_row(row, compatibility))
        payload.setdefault("stream_node_id", self.node_id)
        return EventEnvelope(
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


def resolve_event_subject_from_row(row, compatibility: EventCompatibilityResult | None = None) -> str:
    _ = compatibility
    payload = json.loads(row["payload_json"] or "{}")
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
        replay_checksum="",
        payload_checksum=str(row["payload_checksum"]),
        governance_context=payload.get("governance_context", {}),
        evaluation_context=payload.get("evaluation_context", {}),
        worker_generation=int(payload.get("worker_generation", 0) or 0),
        snapshot_reference=payload.get("snapshot_reference"),
        payload=payload,
        created_at=str(row["created_at"]),
    )
    return resolve_event_subject(envelope)
