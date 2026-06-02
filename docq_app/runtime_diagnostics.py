from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict

from .contracts import (
    AnomalyEvidence,
    AnomalyScore,
    EventType,
    FailureClassificationResult,
    FailurePattern,
    FailureSignature,
    IncidentBlastRadius,
    IncidentCorrelation,
    IncidentEvidenceChain,
    ReplayChecksum,
    ReplayIntegrityResult,
    ReplayInvariantViolation,
    RootCauseEvidence,
    ToolExecutionTelemetry,
    ToolFailureClassification,
    ToolHealthSnapshot,
    ToolLatencyProfile,
    WorkflowAnomaly,
    WorkflowEventRecord,
    WorkflowReplay,
)


def percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(min(math.ceil((pct / 100) * len(ordered)) - 1, len(ordered) - 1), 0)
    return float(ordered[index])


def canonical_replay_payload(replay: WorkflowReplay) -> list[dict[str, object]]:
    return [
        {
            "event_id": step.event_id,
            "trace_id": step.trace_id,
            "correlation_id": step.correlation_id,
            "causation_id": step.causation_id,
            "parent_event_id": step.parent_event_id,
            "root_event_id": step.root_event_id,
            "causation_depth": step.causation_depth,
            "replay_branch_id": step.replay_branch_id,
            "state": step.state,
            "type": step.type.value,
            "action": step.action,
            "decision": step.decision,
            "severity": step.severity,
            "feature_snapshot_hash": str(step.payload.get("feature_snapshot_hash", "")),
            "model_input_hash": str(step.payload.get("model_input_hash", "")),
            "threshold_profile_id": step.payload.get("threshold_profile_id"),
            "model_key": str(step.payload.get("model_key", "")),
            "model_version": str(step.payload.get("model_version", "")),
        }
        for step in replay.steps
    ]


def build_replay_checksum(replay: WorkflowReplay) -> ReplayChecksum:
    canonical = json.dumps(canonical_replay_payload(replay), sort_keys=True, separators=(",", ":"))
    return ReplayChecksum(value=hashlib.sha256(canonical.encode("utf-8")).hexdigest())


def build_lineage_graph(events: list[WorkflowEventRecord]) -> dict[int, list[int]]:
    graph: dict[int, list[int]] = defaultdict(list)
    for event in events:
        if event.parent_event_id is not None:
            graph[event.parent_event_id].append(event.event_id)
        else:
            graph.setdefault(event.event_id, [])
    return dict(graph)


def reconstruct_forward_path(events: list[WorkflowEventRecord], root_event_id: int | None) -> list[int]:
    if root_event_id is None:
        return []
    by_parent = build_lineage_graph(events)
    ordered: list[int] = []
    stack = [root_event_id]
    while stack:
        current = stack.pop(0)
        ordered.append(current)
        stack.extend(sorted(by_parent.get(current, [])))
    return ordered


def reconstruct_reverse_path(events: list[WorkflowEventRecord], event_id: int | None) -> list[int]:
    if event_id is None:
        return []
    by_id = {event.event_id: event for event in events}
    ordered: list[int] = []
    current = by_id.get(event_id)
    while current is not None:
        ordered.append(current.event_id)
        current = by_id.get(current.parent_event_id) if current.parent_event_id is not None else None
    return ordered


def isolate_divergence_branches(replay: WorkflowReplay) -> dict[str, list[int]]:
    branches: dict[str, list[int]] = defaultdict(list)
    for step in replay.steps:
        branches[step.replay_branch_id].append(step.event_id)
    return dict(branches)


def reconstruct_recovery_chain(replay: WorkflowReplay) -> list[int]:
    return [step.event_id for step in replay.steps if step.type == EventType.RECOVERY_TRIGGERED]


def verify_replay_integrity(workflow_id: str, replay_a: WorkflowReplay, replay_b: WorkflowReplay) -> ReplayIntegrityResult:
    violations: list[ReplayInvariantViolation] = []
    checksum_a = build_replay_checksum(replay_a)
    checksum_b = build_replay_checksum(replay_b)
    replay_match = checksum_a.value == checksum_b.value
    if not replay_match:
        violations.append(
            ReplayInvariantViolation(
                invariant="replay_checksum",
                severity="critical",
                detail="Canonical replay checksums differ between deterministic replay passes.",
            )
        )
    if replay_a.step_count != replay_b.step_count:
        violations.append(
            ReplayInvariantViolation(
                invariant="event_count",
                severity="critical",
                detail=f"Replay event count drifted from {replay_a.step_count} to {replay_b.step_count}.",
            )
        )
    lineage_consistency = True
    policy_consistency = True
    retry_consistency = True
    feature_hash_consistency = True
    model_input_consistency = True
    for step_a, step_b in zip(replay_a.steps, replay_b.steps):
        if (
            step_a.event_id != step_b.event_id
            or step_a.parent_event_id != step_b.parent_event_id
            or step_a.root_event_id != step_b.root_event_id
            or step_a.causation_depth != step_b.causation_depth
        ):
            lineage_consistency = False
        if step_a.decision != step_b.decision:
            policy_consistency = False
        if step_a.type == EventType.RECOVERY_TRIGGERED and step_b.type != EventType.RECOVERY_TRIGGERED:
            retry_consistency = False
        if step_a.payload.get("feature_snapshot_hash") != step_b.payload.get("feature_snapshot_hash"):
            feature_hash_consistency = False
        if step_a.payload.get("model_input_hash") != step_b.payload.get("model_input_hash"):
            model_input_consistency = False
    if not lineage_consistency:
        violations.append(
            ReplayInvariantViolation(
                invariant="lineage_order",
                severity="critical",
                detail="Replay lineage order or causation chain drifted between deterministic passes.",
            )
        )
    if not policy_consistency:
        violations.append(
            ReplayInvariantViolation(
                invariant="policy_decisions",
                severity="critical",
                detail="Policy decision sequence changed across replay verification.",
            )
        )
    if not retry_consistency or reconstruct_recovery_chain(replay_a) != reconstruct_recovery_chain(replay_b):
        retry_consistency = False
        violations.append(
            ReplayInvariantViolation(
                invariant="retry_sequence",
                severity="warning",
                detail="Recovery and retry sequence is not stable across replay verification.",
            )
        )
    if not feature_hash_consistency:
        violations.append(
            ReplayInvariantViolation(
                invariant="feature_snapshot_hash",
                severity="critical",
                detail="Immutable feature lineage drifted between replay verification passes.",
            )
        )
    if not model_input_consistency:
        violations.append(
            ReplayInvariantViolation(
                invariant="model_input_hash",
                severity="critical",
                detail="Model input lineage drifted between replay verification passes.",
            )
        )
    for replay in (replay_a, replay_b):
        for step in replay.steps:
            if step.parent_event_id is not None and step.parent_event_id >= step.event_id:
                violations.append(
                    ReplayInvariantViolation(
                        invariant="causation_order",
                        severity="critical",
                        detail=f"Event {step.event_id} has an invalid parent ordering relationship.",
                    )
                )
                lineage_consistency = False
                break
    confidence = max(0.0, 100.0 - (len(violations) * 18.0))
    return ReplayIntegrityResult(
        workflow_id=workflow_id,
        replay_match=replay_match and not violations,
        divergence_detected=not replay_match or bool(violations),
        invariant_violations=violations,
        checksum=checksum_a,
        replay_event_count=replay_a.step_count,
        lineage_consistency=lineage_consistency,
        policy_consistency=policy_consistency,
        retry_consistency=retry_consistency,
        feature_hash_consistency=feature_hash_consistency,
        model_input_consistency=model_input_consistency,
        integrity_confidence=confidence,
    )


def build_tool_latency_profiles(tool_logs: list[ToolExecutionTelemetry]) -> list[ToolLatencyProfile]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for row in tool_logs:
        grouped[row.tool_name].append(int(row.latency_ms))
    profiles = []
    for tool_name, values in sorted(grouped.items()):
        profiles.append(
            ToolLatencyProfile(
                tool_name=tool_name,
                p50_ms=percentile(values, 50),
                p95_ms=percentile(values, 95),
                max_ms=max(values) if values else 0,
            )
        )
    return profiles


def build_tool_failure_classifications(tool_logs: list[ToolExecutionTelemetry]) -> list[ToolFailureClassification]:
    grouped: dict[str, list[ToolExecutionTelemetry]] = defaultdict(list)
    for row in tool_logs:
        grouped[row.tool_name].append(row)
    results = []
    for tool_name, rows in sorted(grouped.items()):
        timeout_count = sum(1 for row in rows if row.error and "timeout" in row.error.lower())
        retry_exhaustion_count = sum(1 for row in rows if row.error and "exhaust" in row.error.lower())
        degraded_window = sum(1 for row in rows if not row.success) > 0 or timeout_count > 0
        taxonomy = "stable"
        if timeout_count > 0:
            taxonomy = "timeout_cascade"
        elif retry_exhaustion_count > 0:
            taxonomy = "retry_exhaustion"
        elif degraded_window:
            taxonomy = "degraded_window"
        results.append(
            ToolFailureClassification(
                tool_name=tool_name,
                timeout_count=timeout_count,
                retry_exhaustion_count=retry_exhaustion_count,
                degraded_window=degraded_window,
                taxonomy=taxonomy,
            )
        )
    return results


def classify_failure_signatures(
    *,
    review_pressure_pct: float,
    emergency_pressure_pct: float,
    retry_pressure_pct: float,
    recovery_success_rate: float,
    failed_notifications: int,
    retrying_notifications: int,
    tool_health: list[ToolHealthSnapshot],
    anomalies: list[WorkflowAnomaly],
    incident_level: str,
) -> FailureClassificationResult:
    signatures: list[FailureSignature] = []
    degraded_tools = [tool.name for tool in tool_health if tool.status == "degraded"]
    if retry_pressure_pct >= 25 or retrying_notifications >= 3:
        signatures.append(
            FailureSignature(
                signature_id="retry-saturation",
                severity="warning",
                confidence=83.0,
                evidence_chain=[RootCauseEvidence(signal="retry_pressure", detail=f"Retry pressure at {retry_pressure_pct}%.")],
                affected_tools=degraded_tools,
                correlated_incidents=[incident_level],
                pattern=FailurePattern(
                    pattern_key="retry_saturation",
                    threshold=25.0,
                    observed_value=retry_pressure_pct,
                    detail="Recovery pressure exceeded the retry saturation threshold.",
                ),
            )
        )
    if degraded_tools:
        signatures.append(
            FailureSignature(
                signature_id="tool-timeout-cascade",
                severity="critical" if "critical" == incident_level else "warning",
                confidence=81.0,
                evidence_chain=[RootCauseEvidence(signal="degraded_tool", detail=f"Degraded tools: {', '.join(degraded_tools)}.")],
                affected_tools=degraded_tools,
                correlated_incidents=[incident_level],
                pattern=FailurePattern(
                    pattern_key="tool_timeout_cascade",
                    threshold=1.0,
                    observed_value=float(len(degraded_tools)),
                    detail="Degraded tool windows indicate timeout or failure cascade behavior.",
                ),
            )
        )
    if emergency_pressure_pct >= 20:
        signatures.append(
            FailureSignature(
                signature_id="escalation-surge",
                severity="critical",
                confidence=79.0,
                evidence_chain=[RootCauseEvidence(signal="emergency_pressure", detail=f"Emergency pressure reached {emergency_pressure_pct}%.")],
                correlated_incidents=[incident_level],
                pattern=FailurePattern(
                    pattern_key="escalation_surge",
                    threshold=20.0,
                    observed_value=emergency_pressure_pct,
                    detail="Escalation density exceeded the configured surge threshold.",
                ),
            )
        )
    if review_pressure_pct >= 50 and retry_pressure_pct >= 15:
        signatures.append(
            FailureSignature(
                signature_id="queue-pressure-amplification",
                severity="warning",
                confidence=77.0,
                evidence_chain=[
                    RootCauseEvidence(signal="review_pressure", detail=f"Review pressure at {review_pressure_pct}%."),
                    RootCauseEvidence(signal="retry_pressure", detail=f"Retry pressure at {retry_pressure_pct}%."),
                ],
                correlated_incidents=[incident_level],
                pattern=FailurePattern(
                    pattern_key="queue_pressure_amplification",
                    threshold=50.0,
                    observed_value=review_pressure_pct,
                    detail="Queue pressure rose concurrently with recovery pressure.",
                ),
            )
        )
    if failed_notifications > 0 or recovery_success_rate < 85:
        signatures.append(
            FailureSignature(
                signature_id="notification-degradation-chain",
                severity="warning",
                confidence=84.0,
                evidence_chain=[
                    RootCauseEvidence(signal="failed_notifications", detail=f"{failed_notifications} notifications failed."),
                    RootCauseEvidence(signal="recovery_success_rate", detail=f"Recovery success rate at {recovery_success_rate}%."),
                ],
                affected_tools=["notification_delivery"],
                correlated_incidents=[incident_level],
                pattern=FailurePattern(
                    pattern_key="notification_degradation_chain",
                    threshold=85.0,
                    observed_value=recovery_success_rate,
                    detail="Notification recovery fell below the acceptable success threshold.",
                ),
            )
        )
    if failed_notifications > 0 and retrying_notifications > 0:
        signatures.append(
            FailureSignature(
                signature_id="recovery-exhaustion",
                severity="critical",
                confidence=86.0,
                evidence_chain=[RootCauseEvidence(signal="recovery_exhaustion", detail="Failed and retrying notifications are present simultaneously.")],
                affected_tools=["notification_delivery"],
                correlated_incidents=[incident_level],
                pattern=FailurePattern(
                    pattern_key="recovery_exhaustion",
                    threshold=1.0,
                    observed_value=float(failed_notifications + retrying_notifications),
                    detail="Recovery attempts are failing to clear the retry queue.",
                ),
            )
        )
    if len(anomalies) >= 2:
        signatures.append(
            FailureSignature(
                signature_id="replay-divergence-cluster",
                severity="warning",
                confidence=73.0,
                evidence_chain=[RootCauseEvidence(signal="anomaly_cluster", detail=f"{len(anomalies)} workflow anomalies detected concurrently.")],
                affected_workflows=[item.workflow_id for item in anomalies[:5]],
                correlated_incidents=[incident_level],
                pattern=FailurePattern(
                    pattern_key="replay_divergence_cluster",
                    threshold=2.0,
                    observed_value=float(len(anomalies)),
                    detail="Anomalous workflows are clustering beyond baseline expectations.",
                ),
            )
        )
    return FailureClassificationResult(signatures=signatures)


def classify_workflow_anomalies(
    workflow_profiles: list[dict[str, float | str]],
    *,
    baseline_duration: float,
    baseline_retry: float,
    baseline_latency: float,
) -> list[WorkflowAnomaly]:
    anomalies: list[WorkflowAnomaly] = []
    for profile in workflow_profiles:
        evidence: list[AnomalyEvidence] = []
        score = 0.0
        duration = float(profile.get("duration_minutes", 0.0))
        retries = float(profile.get("retry_count", 0.0))
        latency = float(profile.get("latency_ms", 0.0))
        if baseline_duration > 0 and duration > baseline_duration * 1.5:
            deviation = duration - baseline_duration
            evidence.append(
                AnomalyEvidence(
                    metric="workflow_duration",
                    observed=duration,
                    baseline=baseline_duration,
                    deviation=deviation,
                    detail="Workflow duration exceeded rolling baseline.",
                )
            )
            score += deviation
        if baseline_retry > 0 and retries > max(1.0, baseline_retry * 1.5):
            deviation = retries - baseline_retry
            evidence.append(
                AnomalyEvidence(
                    metric="retry_frequency",
                    observed=retries,
                    baseline=baseline_retry,
                    deviation=deviation,
                    detail="Retry frequency exceeded rolling baseline.",
                )
            )
            score += deviation * 20.0
        if baseline_latency > 0 and latency > baseline_latency * 1.75:
            deviation = latency - baseline_latency
            evidence.append(
                AnomalyEvidence(
                    metric="tool_latency",
                    observed=latency,
                    baseline=baseline_latency,
                    deviation=deviation,
                    detail="Tool latency exceeded rolling baseline.",
                )
            )
            score += deviation / 10.0
        if not evidence:
            continue
        severity = "watch"
        if score >= 80:
            severity = "critical"
        elif score >= 40:
            severity = "elevated"
        anomalies.append(
            WorkflowAnomaly(
                workflow_id=str(profile.get("workflow_id", "")),
                category="lineage_deviation",
                lineage_marker=str(profile.get("correlation_id", "")),
                score=AnomalyScore(score=round(score, 1), severity=severity),
                evidence=evidence,
            )
        )
    return anomalies


def correlate_incident(
    *,
    incident_level: str,
    degraded_tools: list[str],
    failed_notifications: int,
    retrying_notifications: int,
    review_pressure_pct: float,
    emergency_pressure_pct: float,
    anomalies: list[WorkflowAnomaly],
) -> IncidentCorrelation:
    probable_source = "nominal orchestration state"
    evidence: list[IncidentEvidenceChain] = []
    affected_subsystems: list[str] = []
    if degraded_tools:
        probable_source = "tool degradation cascade"
        affected_subsystems.extend(degraded_tools)
        evidence.append(IncidentEvidenceChain(signal="degraded_tools", detail=f"Degraded tools: {', '.join(degraded_tools)}."))
    if failed_notifications > 0 or retrying_notifications > 0:
        probable_source = "notification recovery instability"
        affected_subsystems.append("notification_delivery")
        evidence.append(
            IncidentEvidenceChain(
                signal="notification_recovery",
                detail=f"{retrying_notifications} retrying and {failed_notifications} failed notification jobs detected.",
            )
        )
    if review_pressure_pct >= 50 or emergency_pressure_pct >= 20:
        probable_source = "queue pressure amplification"
        affected_subsystems.append("workflow_queue")
        evidence.append(
            IncidentEvidenceChain(
                signal="queue_pressure",
                detail=f"Review pressure {review_pressure_pct}% and emergency pressure {emergency_pressure_pct}%.",
            )
        )
    if anomalies:
        evidence.append(
            IncidentEvidenceChain(
                signal="workflow_anomalies",
                detail=f"{len(anomalies)} anomalous workflow profiles detected.",
            )
        )
    blast_radius = IncidentBlastRadius(
        affected_workflow_count=len(anomalies),
        affected_tool_count=len(set(affected_subsystems)),
        affected_subsystems=sorted(set(affected_subsystems)),
    )
    return IncidentCorrelation(
        probable_incident_source=probable_source,
        affected_subsystems=sorted(set(affected_subsystems)),
        degradation_severity=incident_level,
        blast_radius=blast_radius,
        evidence_chain=evidence,
    )


def build_migration_audit(event_version: str, normalized_event: dict[str, object]) -> dict[str, object]:
    migrated_fields = [field for field in ("timestamp", "state", "trace_id", "correlation_id", "root_event_id") if field in normalized_event]
    return {
        "event_version": event_version,
        "compatible": True,
        "migrated_fields": migrated_fields,
        "detail": "Event normalized against the canonical registry.",
    }
