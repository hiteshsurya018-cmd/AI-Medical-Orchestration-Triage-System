from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import EVENT_SCHEMA_VERSION, EventType


def normalize_workflow_event(raw: Mapping[str, Any]) -> dict[str, Any]:
    version = str(raw.get("version") or EVENT_SCHEMA_VERSION.value)
    canonical_fields = {"timestamp", "state", "trace_id", "correlation_id", "type"}
    if version == EVENT_SCHEMA_VERSION.value and canonical_fields.issubset(raw.keys()):
        return dict(raw)
    migration = MIGRATION_REGISTRY.get(version)
    if migration is None:
        return migrate_event_to_v1(raw)
    return migration(raw)


def migrate_event_to_v1(raw: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(raw)
    payload["version"] = EVENT_SCHEMA_VERSION.value
    payload["timestamp"] = payload.get("timestamp") or payload.get("created_at") or ""
    payload["state"] = payload.get("state") or payload.get("stage") or "unknown"
    payload["trace_id"] = str(payload.get("trace_id") or payload.get("workflow_id") or "")
    payload["correlation_id"] = str(payload.get("correlation_id") or payload.get("workflow_id") or "")
    payload["causation_id"] = payload.get("causation_id")
    payload["parent_event_id"] = payload.get("parent_event_id") or payload.get("causation_id")
    payload["root_event_id"] = payload.get("root_event_id")
    payload["causation_depth"] = int(payload.get("causation_depth") or 0)
    payload["replay_branch_id"] = str(payload.get("replay_branch_id") or "main")
    payload["type"] = payload.get("type") or EventType.WORKFLOW_TRANSITION.value
    payload["payload"] = dict(payload.get("payload") or {})
    return payload


def validate_event_compatibility(raw: Mapping[str, Any]) -> bool:
    normalized = normalize_workflow_event(raw)
    required = {"workflow_id", "timestamp", "state", "trace_id", "correlation_id", "type"}
    return required.issubset(normalized.keys())


MIGRATION_REGISTRY: dict[str, Any] = {
    "v1": migrate_event_to_v1,
}
