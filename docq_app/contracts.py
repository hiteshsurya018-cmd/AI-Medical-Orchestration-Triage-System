from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class EventVersion(str, Enum):
    V1 = "v1"


class EventSchemaVersion(str, Enum):
    V1 = "v1"


class EventType(str, Enum):
    AGENT_OBSERVATION = "agent_observation"
    POLICY_DECISION = "policy_decision"
    TOOL_INVOKED = "tool_invoked"
    RECOVERY_TRIGGERED = "recovery_triggered"
    COMMUNICATION_PREPARED = "communication_prepared"
    WORKFLOW_TRANSITION = "workflow_transition"


EVENT_SCHEMA_VERSION = EventVersion.V1
WorkflowDecision = Literal["pending", "follow_up_questions", "autonomous_booking", "human_review", "emergency_escalation", "unknown"]
WorkflowSeverity = Literal["info", "warning", "critical", "success"]
WorkflowEventType = Literal[
    EventType.AGENT_OBSERVATION,
    EventType.POLICY_DECISION,
    EventType.TOOL_INVOKED,
    EventType.RECOVERY_TRIGGERED,
    EventType.COMMUNICATION_PREPARED,
    EventType.WORKFLOW_TRANSITION,
]


class PolicyDecision(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    action: WorkflowDecision
    confidence: float = Field(ge=0.0, le=100.0)
    reasons: list[str] = Field(default_factory=list)
    human_review_required: bool = False


class WorkflowEventRecord(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    event_id: int
    workflow_id: str
    trace_id: str
    correlation_id: str
    causation_id: int | None = None
    parent_event_id: int | None = None
    root_event_id: int | None = None
    causation_depth: int = 0
    replay_branch_id: str = "main"
    timestamp: str
    type: EventType
    severity: WorkflowSeverity = "info"
    agent: str
    state: str
    action: str
    decision: str = "pending"
    confidence: float | None = None
    reasons: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowReplayStep(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    event_id: int
    workflow_id: str
    trace_id: str
    correlation_id: str
    causation_id: int | None = None
    parent_event_id: int | None = None
    root_event_id: int | None = None
    causation_depth: int = 0
    replay_branch_id: str = "main"
    timestamp: str
    type: EventType
    severity: WorkflowSeverity = "info"
    agent: str
    state: str
    action: str
    decision: str = "pending"
    confidence: float | None = None
    reasons: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowReplay(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    workflow_id: str
    step_count: int
    latest_decision: str = "unknown"
    steps: list[WorkflowReplayStep] = Field(default_factory=list)


class ReplaySnapshot(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    id: int | None = None
    workflow_id: str
    replay_branch_id: str = "main"
    snapshot_event_id: int
    snapshot_checksum: str
    workflow_state_blob: dict[str, Any] = Field(default_factory=dict)
    lineage_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    invalidated_at: str | None = None


class SnapshotIntegrityCheck(BaseModel):
    snapshot_id: int | None = None
    valid: bool = True
    expected_checksum: str = ""
    actual_checksum: str = ""
    detail: str = ""


class HydrationLineageSummary(BaseModel):
    workflow_id: str
    replay_branch_id: str = "main"
    root_event_id: int | None = None
    snapshot_step_count: int = 0
    incremental_step_count: int = 0
    total_step_count: int = 0


class ReplayCheckpoint(BaseModel):
    workflow_id: str
    replay_branch_id: str = "main"
    snapshot_id: int | None = None
    checkpoint_event_id: int | None = None
    hydration_generation: int = 0
    checkpoint_checksum: str = ""


class SnapshotHydrationResult(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    workflow_id: str
    snapshot_hit: bool = False
    replay: WorkflowReplay
    snapshot: ReplaySnapshot | None = None
    integrity: SnapshotIntegrityCheck | None = None
    checkpoint: ReplayCheckpoint | None = None
    lineage_summary: HydrationLineageSummary


class RetentionPolicyContract(BaseModel):
    table_name: str
    strategy: Literal["monthly", "quarterly", "none"] = "monthly"
    retention_days: int = 90
    archive_enabled: bool = True


class WorkerLease(BaseModel):
    id: int | None = None
    worker_id: str
    task_id: str
    workflow_id: str
    lease_token: str
    lease_expiration: str
    retry_generation: int = 0
    execution_checksum: str
    created_at: str
    updated_at: str | None = None


class WorkerLeaseResult(BaseModel):
    acquired: bool = False
    lease: WorkerLease | None = None
    reason: str = ""


class IntelligenceRollup(BaseModel):
    id: int | None = None
    rollup_key: str
    rollup_type: str
    rollup_scope: str = "global"
    source_checksum: str
    rollup_checksum: str
    payload_json: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class RollupGenerationMetadata(BaseModel):
    id: int | None = None
    rollup_key: str
    generation_status: Literal["running", "completed", "failed"] = "completed"
    workflow_count: int = 0
    rollup_checksum: str = ""
    created_at: str
    completed_at: str | None = None


class EventEnvelope(BaseModel):
    schema_version: EventSchemaVersion = EventSchemaVersion.V1
    event_id: int
    event_type: str
    aggregate_id: str
    workflow_id: str
    trace_id: str
    root_event_id: int | None = None
    causation_id: int | None = None
    replay_branch_id: str = "main"
    replay_checksum: str = ""
    payload_checksum: str
    governance_context: dict[str, Any] = Field(default_factory=dict)
    evaluation_context: dict[str, Any] = Field(default_factory=dict)
    worker_generation: int = 0
    snapshot_reference: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class EventCompatibilityResult(BaseModel):
    schema_version: EventSchemaVersion = EventSchemaVersion.V1
    compatible: bool = True
    detail: str = ""
    missing_fields: list[str] = Field(default_factory=list)


class EventDeliveryRecord(BaseModel):
    id: int | None = None
    outbox_id: int
    consumer_id: str
    event_id: int
    processing_checksum: str
    delivery_status: Literal["pending", "processed", "failed"] = "processed"
    retry_generation: int = 0
    lease_token: str = ""
    created_at: str
    updated_at: str | None = None


class ConsumerCheckpoint(BaseModel):
    id: int | None = None
    consumer_id: str
    last_outbox_id: int = 0
    last_event_id: int = 0
    checkpoint_checksum: str = ""
    updated_at: str


class ProjectionCheckpoint(BaseModel):
    id: int | None = None
    projection_name: str
    projection_scope: str = "global"
    source_outbox_id: int = 0
    source_event_id: int = 0
    projection_generation: int = 0
    projection_checksum: str = ""
    replay_lineage_metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: str


class WorkflowMetricsSummary(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    active_workflows: int = 0
    human_review_queue: int = 0
    emergency_escalations: int = 0
    autonomous_bookings: int = 0
    failed_recoveries: int = 0
    average_confidence: float = 0.0
    decision_breakdown: list[dict[str, object]] = Field(default_factory=list)
    activity_feed: list[WorkflowEventRecord] = Field(default_factory=list)


class AppointmentLifecycleTransition(BaseModel):
    id: int | None = None
    appointment_id: int
    workflow_id: str
    from_state: str
    to_state: str
    cause: str
    responsible_actor: str
    responsible_role: str
    event_id: int | None = None
    sla_due_at: str | None = None
    escalation_lineage: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ReminderWorkflowState(BaseModel):
    appointment_id: int
    reminder_type: str
    reminder_status: str
    attempts: int = 0
    next_attempt_at: str | None = None
    acknowledged_at: str | None = None
    last_error: str | None = None


class SlaViolation(BaseModel):
    id: int | None = None
    appointment_id: int | None = None
    workflow_id: str
    sla_type: str
    threshold_minutes: int
    observed_minutes: int
    action_triggered: str
    violation_status: str
    evidence_json: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class CoordinationQueueItem(BaseModel):
    id: int | None = None
    queue_type: str
    appointment_id: int | None = None
    workflow_id: str
    priority: int = 0
    queue_status: str
    assigned_owner: str | None = None
    causation_lineage: dict[str, Any] = Field(default_factory=dict)
    payload_json: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class CalendarSyncState(BaseModel):
    id: int | None = None
    appointment_id: int
    provider: str
    sync_direction: str
    sync_status: str
    external_ref: str | None = None
    conflict_detected: bool = False
    retry_count: int = 0
    payload_json: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class QueuePressureSnapshot(BaseModel):
    review_pressure_pct: float = 0.0
    emergency_pressure_pct: float = 0.0
    retry_pressure_pct: float = 0.0
    unresolved_workflows: int = 0
    pressure_level: Literal["stable", "watch", "elevated", "critical"] = "stable"


class FeatureSnapshotContract(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    id: int | None = None
    workflow_id: str
    patient_id: str | None = None
    conversation_id: str
    model_family: str
    feature_version: str
    feature_snapshot_hash: str
    model_input_hash: str
    created_at: str
    symptom_text: str
    structured_features_json: dict[str, Any] = Field(default_factory=dict)
    temporal_features_json: dict[str, Any] = Field(default_factory=dict)
    text_features_hash: str
    label_status: str = "unlabeled"
    label_source: str = ""
    label_updated_at: str | None = None


class ThresholdProfileContract(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    id: int | None = None
    profile_key: str
    thresholds_json: dict[str, float] = Field(default_factory=dict)
    status: Literal["candidate", "active", "retired"] = "candidate"
    created_at: str
    promoted_at: str | None = None


class RiskPredictionContract(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    id: int | None = None
    workflow_id: str
    feature_snapshot_id: int
    model_registry_id: int
    created_at: str
    raw_score: float = Field(ge=0.0, le=1.0)
    calibrated_score: float = Field(ge=0.0, le=1.0)
    risk_band: Literal["low", "medium", "high", "emergency"]
    predicted_specialty: str
    predicted_urgency: str
    predicted_severity: str
    requires_review: bool = False
    threshold_profile_id: int
    feature_snapshot_hash: str
    model_input_hash: str
    model_key: str
    model_version: str
    feature_version: str
    active_model_key: str = ""
    candidate_model_key: str = ""
    explanations_json: dict[str, Any] = Field(default_factory=dict)
    top_features_json: list[dict[str, Any]] = Field(default_factory=list)
    is_shadow_prediction: bool = False


class ShadowPredictionComparison(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    workflow_id: str
    active_model_key: str
    candidate_model_key: str
    active_prediction: RiskPredictionContract
    candidate_prediction: RiskPredictionContract
    divergence_summary: str = ""
    confidence_delta: float = 0.0
    risk_band_delta: str = ""
    threshold_trigger_delta: str = ""
    review_recommendation_delta: bool = False
    policy_impact_delta: str = ""
    escalation_delta: bool = False
    replay_safe_explanation_payload: dict[str, Any] = Field(default_factory=dict)


class DriftMetricSnapshot(BaseModel):
    metric_key: str
    value: float = 0.0
    baseline: float = 0.0
    delta: float = 0.0
    severity: Literal["nominal", "watch", "elevated", "critical"] = "nominal"
    detail: str = ""


class DriftDetectionSummary(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    active_model_key: str = ""
    candidate_model_key: str = ""
    generated_at: str
    metrics: list[DriftMetricSnapshot] = Field(default_factory=list)


class ModelGovernanceSummary(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    active_model_key: str = ""
    candidate_model_key: str = ""
    active_threshold_profile_id: int | None = None
    candidate_threshold_profile_id: int | None = None
    latest_feature_snapshot_hash: str = ""
    latest_model_input_hash: str = ""
    shadow_prediction_count: int = 0
    divergent_shadow_predictions: int = 0
    latest_shadow_comparison: ShadowPredictionComparison | None = None
    latest_drift_summary: DriftDetectionSummary | None = None
    latest_evaluation_run_id: int | None = None
    latest_evaluation_status: str = ""
    promotion_readiness: str = ""
    governance_state: str = "stable"
    active_recommendation_count: int = 0
    rollout_risk_score: float = 0.0
    rollback_risk_score: float = 0.0


class CandidatePolicyDelta(BaseModel):
    workflow_id: str
    active_policy_path: str
    candidate_policy_path: str
    escalation_delta: bool = False
    review_delta: bool = False
    threshold_delta: str = ""
    severity_delta: str = ""
    specialty_delta: str = ""
    calibration_delta: float = 0.0
    false_negative_risk: bool = False


class ThresholdSimulationResult(BaseModel):
    workflow_id: str
    active_threshold_profile_id: int
    candidate_threshold_profile_id: int
    active_threshold_profile_key: str = ""
    candidate_threshold_profile_key: str = ""
    threshold_trigger_delta: str = ""
    escalation_sensitivity_delta: float = 0.0
    review_sensitivity_delta: float = 0.0


class ReplaySimulationResult(BaseModel):
    workflow_id: str
    replay_integrity_passed: bool = True
    evaluation_checksum: str
    feature_snapshot_hash: str
    model_input_hash: str
    active_prediction: RiskPredictionContract
    candidate_prediction: RiskPredictionContract
    policy_delta: CandidatePolicyDelta
    threshold_simulation: ThresholdSimulationResult
    divergence_summary_json: dict[str, Any] = Field(default_factory=dict)


class ModelEvaluationResult(BaseModel):
    id: int | None = None
    evaluation_run_id: int
    workflow_id: str
    feature_snapshot_id: int
    replay_integrity_status: str
    active_prediction_id: int
    candidate_prediction_id: int
    active_policy_path: str
    candidate_policy_path: str
    escalation_delta: bool = False
    review_delta: bool = False
    threshold_delta: str = ""
    severity_delta: str = ""
    specialty_delta: str = ""
    calibration_delta: float = 0.0
    false_negative_risk: bool = False
    divergence_summary_json: dict[str, Any] = Field(default_factory=dict)


class EvaluationDriftSnapshot(BaseModel):
    id: int | None = None
    evaluation_run_id: int
    score_distribution_delta: float = 0.0
    specialty_distribution_delta: float = 0.0
    review_rate_delta: float = 0.0
    escalation_delta: float = 0.0
    false_negative_delta: float = 0.0
    calibration_error_delta: float = 0.0
    created_at: str


class PromotionGateResult(BaseModel):
    profile_key: str
    passed: bool = False
    violated_rules: list[str] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)
    confidence_metrics: dict[str, float] = Field(default_factory=dict)
    recommendation_summary: str = ""


class EvaluationSummary(BaseModel):
    evaluation_checksum: str
    replay_integrity_passed: bool = True
    divergence_count: int = 0
    escalation_delta: float = 0.0
    false_negative_delta: float = 0.0
    review_rate_delta: float = 0.0
    calibration_delta: float = 0.0
    promotion_recommendation: str = "hold"


class ModelEvaluationRun(BaseModel):
    id: int | None = None
    evaluation_run_key: str
    candidate_model_registry_id: int
    active_model_registry_id: int
    candidate_threshold_profile_id: int
    active_threshold_profile_id: int
    evaluation_scope: str
    workflow_count: int = 0
    started_at: str
    completed_at: str | None = None
    status: Literal["running", "completed", "failed", "invalidated"] = "running"
    replay_integrity_passed: bool = True
    evaluation_checksum: str = ""
    summary_json: EvaluationSummary = Field(default_factory=lambda: EvaluationSummary(evaluation_checksum=""))
    promotion_recommendation: str = "hold"
    promotion_gate_result: PromotionGateResult | None = None


class GovernanceIncidentCorrelation(BaseModel):
    incident_correlation_id: str = ""
    source: str = ""
    severity: Literal["stable", "watch", "elevated", "critical"] = "stable"
    detail: str = ""


class GovernanceRecommendation(BaseModel):
    id: int | None = None
    recommendation_key: str
    recommendation_type: Literal["promotion", "rollback", "reevaluation", "threshold_adjustment", "drift_alert"]
    source_evaluation_run_id: int | None = None
    candidate_model_registry_id: int | None = None
    threshold_profile_id: int | None = None
    recommendation_status: Literal["pending", "approved", "rejected", "expired", "superseded"] = "pending"
    recommendation_reason: str
    confidence_score: float = Field(ge=0.0, le=100.0, default=0.0)
    created_at: str
    resolved_at: str | None = None
    supporting_evidence_json: dict[str, Any] = Field(default_factory=dict)


class RolloutSimulationProfile(BaseModel):
    id: int | None = None
    rollout_profile_key: str
    rollout_percentages_json: list[int] = Field(default_factory=list)
    safety_constraints_json: dict[str, float | bool] = Field(default_factory=dict)
    status: str = "active"
    created_at: str


class GovernanceTimelineEvent(BaseModel):
    id: int | None = None
    governance_entity_type: str
    governance_entity_id: int
    event_type: str
    event_timestamp: str
    related_model_key: str = ""
    related_threshold_profile_key: str = ""
    incident_correlation_id: str = ""
    payload_json: dict[str, Any] = Field(default_factory=dict)


class DriftTriggerResult(BaseModel):
    rule_key: str
    drift_metric_type: str
    threshold_value: float
    trigger_action: str
    cooldown_minutes: int = 0
    triggered: bool = False
    drift_trigger_source: str = ""
    governance_checksum: str = ""


class RollbackRecommendation(BaseModel):
    recommendation: GovernanceRecommendation
    rollback_risk_score: float = 0.0
    incident_correlation: GovernanceIncidentCorrelation | None = None


class PromotionRecommendation(BaseModel):
    recommendation: GovernanceRecommendation
    rollout_risk_score: float = 0.0
    incident_correlation: GovernanceIncidentCorrelation | None = None


class GovernanceStateSnapshot(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    governance_checksum: str = ""
    recommendation_confidence: float = 0.0
    rollout_risk_score: float = 0.0
    rollback_risk_score: float = 0.0
    incident_correlation: GovernanceIncidentCorrelation | None = None
    active_recommendations: list[GovernanceRecommendation] = Field(default_factory=list)
    drift_triggers: list[DriftTriggerResult] = Field(default_factory=list)
    rollout_profiles: list[RolloutSimulationProfile] = Field(default_factory=list)
    timeline: list[GovernanceTimelineEvent] = Field(default_factory=list)


class StuckWorkflowSnapshot(BaseModel):
    workflow_id: str
    state: str
    decision: str
    minutes_stalled: int = 0
    severity: WorkflowSeverity = "warning"


class IncidentState(BaseModel):
    active: bool = False
    level: Literal["stable", "watch", "elevated", "critical"] = "stable"
    title: str = "Nominal"
    summary: str = ""
    triggers: list[str] = Field(default_factory=list)


class WorkflowLineageSummary(BaseModel):
    workflow_id: str
    root_event_id: int | None = None
    latest_event_id: int | None = None
    event_count: int = 0
    tool_invocation_count: int = 0
    last_tool_name: str = ""
    correlation_id: str = ""


class ReplayChecksum(BaseModel):
    algorithm: Literal["sha256"] = "sha256"
    value: str


class ReplayInvariantViolation(BaseModel):
    invariant: str
    severity: WorkflowSeverity = "warning"
    detail: str


class ReplayIntegrityResult(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    workflow_id: str
    replay_match: bool = True
    divergence_detected: bool = False
    invariant_violations: list[ReplayInvariantViolation] = Field(default_factory=list)
    checksum: ReplayChecksum
    replay_event_count: int = 0
    lineage_consistency: bool = True
    policy_consistency: bool = True
    retry_consistency: bool = True
    feature_hash_consistency: bool = True
    model_input_consistency: bool = True
    integrity_confidence: float = Field(ge=0.0, le=100.0, default=100.0)


class ReplayDiffDivergence(BaseModel):
    event_index: int = 0
    workflow_a_event_id: int | None = None
    workflow_b_event_id: int | None = None
    workflow_a_action: str = ""
    workflow_b_action: str = ""
    workflow_a_decision: str = ""
    workflow_b_decision: str = ""
    summary: str = ""


class RootCauseEvidence(BaseModel):
    timestamp: str = ""
    signal: str
    detail: str


class RootCauseSummary(BaseModel):
    probable_cause: str
    confidence: float = Field(ge=0.0, le=100.0)
    supporting_events: list[RootCauseEvidence] = Field(default_factory=list)
    divergence_point: int | None = None
    affected_tools: list[str] = Field(default_factory=list)
    retry_correlation: str = ""
    incident_correlation: str = ""


class ReplayDiff(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    workflow_a: str
    workflow_b: str
    divergence_point: int | None = None
    summary: str = ""
    retry_delta: int = 0
    latency_delta_ms: int = 0
    policy_path_delta: str = ""
    tool_outcome_delta: str = ""
    root_cause: RootCauseSummary | None = None
    differing_events: list[ReplayDiffDivergence] = Field(default_factory=list)


class RecoveryMetricsSnapshot(BaseModel):
    retrying_notifications: int = 0
    failed_notifications: int = 0
    fallback_events: int = 0
    sent_notifications: int = 0
    recovery_success_rate: float = 0.0


class FailurePattern(BaseModel):
    pattern_key: str
    threshold: float
    observed_value: float
    detail: str


class FailureSignature(BaseModel):
    signature_id: str
    severity: WorkflowSeverity
    confidence: float = Field(ge=0.0, le=100.0)
    evidence_chain: list[RootCauseEvidence] = Field(default_factory=list)
    affected_tools: list[str] = Field(default_factory=list)
    affected_workflows: list[str] = Field(default_factory=list)
    correlated_incidents: list[str] = Field(default_factory=list)
    pattern: FailurePattern


class FailureClassificationResult(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    signatures: list[FailureSignature] = Field(default_factory=list)


class AnomalyEvidence(BaseModel):
    metric: str
    observed: float
    baseline: float
    deviation: float
    detail: str


class AnomalyScore(BaseModel):
    score: float = Field(ge=0.0)
    severity: Literal["nominal", "watch", "elevated", "critical"] = "nominal"


class WorkflowAnomaly(BaseModel):
    workflow_id: str
    category: str
    lineage_marker: str = ""
    score: AnomalyScore
    evidence: list[AnomalyEvidence] = Field(default_factory=list)


class ToolHealthSnapshot(BaseModel):
    name: str
    status: Literal["healthy", "degraded", "watch"] = "healthy"
    metric_label: str
    metric_value: str
    detail: str


class ToolLatencyProfile(BaseModel):
    tool_name: str
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    max_ms: int = 0


class ToolFailureClassification(BaseModel):
    tool_name: str
    timeout_count: int = 0
    retry_exhaustion_count: int = 0
    degraded_window: bool = False
    taxonomy: str = "stable"


class WorkflowSlaSnapshot(BaseModel):
    avg_resolution_minutes: float = 0.0
    avg_workflow_age_minutes: float = 0.0
    avg_review_age_minutes: float = 0.0


class OperationalAlert(BaseModel):
    severity: WorkflowSeverity
    message: str


class IncidentEvidenceChain(BaseModel):
    signal: str
    detail: str


class IncidentBlastRadius(BaseModel):
    affected_workflow_count: int = 0
    affected_tool_count: int = 0
    affected_subsystems: list[str] = Field(default_factory=list)


class IncidentCorrelation(BaseModel):
    probable_incident_source: str
    affected_subsystems: list[str] = Field(default_factory=list)
    degradation_severity: Literal["stable", "watch", "elevated", "critical"] = "stable"
    blast_radius: IncidentBlastRadius = Field(default_factory=IncidentBlastRadius)
    evidence_chain: list[IncidentEvidenceChain] = Field(default_factory=list)


class MigrationAuditTrail(BaseModel):
    event_version: str
    compatible: bool = True
    migrated_fields: list[str] = Field(default_factory=list)
    detail: str = ""


class WorkflowOperationalIntelligence(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    incident_state: IncidentState = Field(default_factory=IncidentState)
    incident_correlation: IncidentCorrelation | None = None
    queue_pressure: QueuePressureSnapshot = Field(default_factory=QueuePressureSnapshot)
    stuck_workflows: list[StuckWorkflowSnapshot] = Field(default_factory=list)
    lineage_summaries: list[WorkflowLineageSummary] = Field(default_factory=list)
    recovery_metrics: RecoveryMetricsSnapshot = Field(default_factory=RecoveryMetricsSnapshot)
    tool_health: list[ToolHealthSnapshot] = Field(default_factory=list)
    tool_latency_profiles: list[ToolLatencyProfile] = Field(default_factory=list)
    tool_failure_classifications: list[ToolFailureClassification] = Field(default_factory=list)
    failure_signatures: list[FailureSignature] = Field(default_factory=list)
    anomalies: list[WorkflowAnomaly] = Field(default_factory=list)
    sla_metrics: WorkflowSlaSnapshot = Field(default_factory=WorkflowSlaSnapshot)
    alerts: list[OperationalAlert] = Field(default_factory=list)
    model_governance: ModelGovernanceSummary | None = None
    governance_state: GovernanceStateSnapshot | None = None
    lifecycle_summary: dict[str, Any] = Field(default_factory=dict)
    reminder_summary: dict[str, Any] = Field(default_factory=dict)
    sla_summary: dict[str, Any] = Field(default_factory=dict)
    coordination_summary: dict[str, Any] = Field(default_factory=dict)
    calendar_sync_summary: dict[str, Any] = Field(default_factory=dict)


class WorkflowConsoleSnapshot(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    workflow_metrics: WorkflowMetricsSummary
    operational_intelligence: WorkflowOperationalIntelligence = Field(default_factory=WorkflowOperationalIntelligence)
    workflow_replay: WorkflowReplay | None = None
    workflow_diff: ReplayDiff | None = None
    workflow_model_diff: ShadowPredictionComparison | None = None
    workflow_drift: DriftDetectionSummary | None = None
    replay_integrity: ReplayIntegrityResult | None = None
    selected_workflow_id: str = ""
    generated_at: str


class ToolExecutionTelemetry(BaseModel):
    version: EventVersion = EVENT_SCHEMA_VERSION
    invocation_id: str
    workflow_id: str
    trace_id: str
    tool_name: str
    agent: str
    parent_event_id: int | None = None
    replay_branch_id: str = "main"
    latency_ms: int = Field(ge=0)
    success: bool = True
    fallback_used: bool = False
    error: str | None = None
    created_at: str
