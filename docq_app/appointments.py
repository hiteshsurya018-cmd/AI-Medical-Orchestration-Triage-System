from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any

from .compliance import encrypt_sensitive_value
from .constants import DEFAULT_SLOT_TIMES, DOCTOR_ACCOUNTS, SPECIALTY_LABELS
from .contracts import (
    DriftDetectionSummary,
    EvaluationDriftSnapshot,
    EvaluationSummary,
    EventType,
    FeatureSnapshotContract,
    GovernanceRecommendation,
    GovernanceStateSnapshot,
    GovernanceTimelineEvent,
    DriftTriggerResult,
    ModelEvaluationResult,
    ModelEvaluationRun,
    PromotionGateResult,
    ReplayDiff,
    ReplayDiffDivergence,
    RiskPredictionContract,
    RolloutSimulationProfile,
    RootCauseEvidence,
    RootCauseSummary,
    ShadowPredictionComparison,
    ThresholdProfileContract,
    ToolExecutionTelemetry,
    WorkflowEventRecord,
    WorkflowReplay,
)
from .event_migrations import normalize_workflow_event, validate_event_compatibility
from .db import get_connection
from .clinical_questionnaires import format_questionnaire_context
from .clinical_runtime import build_clinical_summary, build_risk_explanation, evaluate_vitals, normalize_vitals
from .ml import analyze_symptoms, determine_queue_state, normalize_specialty
from .ml_governance import build_drift_summary, build_governance_summary, build_shadow_comparison
from .notifications import create_notification, notify_automation
from .pydantic_compat import model_dump
from .repositories import EvaluationRepository, GovernanceRepository, ReplayRepository, TelemetryRepository, WorkflowEventRepository
from .replay_snapshots import hydrate_workflow_replay
from .runtime_diagnostics import build_migration_audit
from .scheduling_engine import (
    build_doctor_calendar,
    compact_available_dates,
    ensure_scheduling_tables,
    rank_doctor_availability,
    reserve_best_slot,
    sync_default_doctor_schedules,
)
from .tenancy import get_current_tenant_key
from .time_utils import get_current_date, get_current_time, is_future_slot

EVALUATION_WORKFLOW_PREFIX = "ml-eval:"
GOVERNANCE_WORKFLOW_PREFIX = "ml-governance:"
SECURITY_WORKFLOW_PREFIX = "security:"

workflow_event_repository = WorkflowEventRepository()
replay_repository = ReplayRepository()
evaluation_repository = EvaluationRepository()
governance_repository = GovernanceRepository()
telemetry_repository = TelemetryRepository()


def infer_workflow_event_type(agent: str, action: str) -> EventType:
    normalized_agent = agent.lower()
    normalized_action = action.lower()
    if normalized_agent == "policy-engine":
        return EventType.POLICY_DECISION
    if "fallback" in normalized_action or "retry" in normalized_action or "recover" in normalized_action:
        return EventType.RECOVERY_TRIGGERED
    if "tool" in normalized_action or "reserve" in normalized_action or "slot" in normalized_action:
        return EventType.TOOL_INVOKED
    if "response" in normalized_action or "notify" in normalized_action or "message" in normalized_action:
        return EventType.COMMUNICATION_PREPARED
    return EventType.WORKFLOW_TRANSITION


def infer_workflow_event_severity(decision: str, action: str, confidence: float | None) -> str:
    normalized_decision = str(decision or "pending").lower()
    normalized_action = action.lower()
    if normalized_decision == "emergency_escalation":
        return "critical"
    if normalized_decision in {"human_review", "follow_up_questions"}:
        return "warning"
    if "failed" in normalized_action or "fallback" in normalized_action or "retry" in normalized_action:
        return "warning"
    if normalized_decision == "autonomous_booking":
        return "success"
    if confidence is not None and float(confidence) < 70.0:
        return "warning"
    return "info"


def build_workflow_event_record(row: sqlite3.Row) -> WorkflowEventRecord:
    reasons = json.loads(row["reasons"] or "[]")
    decision = row["decision"] or "pending"
    confidence = float(row["confidence"]) if row["confidence"] is not None else None
    persisted_payload = json.loads(row["payload_json"] or "{}")
    row_keys = set(row.keys()) if hasattr(row, "keys") else set()
    tenant_key = str(
        (
            row["tenant_key"]
            if "tenant_key" in row_keys and row["tenant_key"] is not None
            else persisted_payload.get("tenant_key")
        )
        or "default-clinic"
    )
    payload = normalize_workflow_event(
        {
            "event_id": int(row["id"]),
            "workflow_id": row["workflow_id"],
            "trace_id": str(row["trace_id"] or row["workflow_id"]),
            "correlation_id": str(row["correlation_id"] or row["workflow_id"]),
            "causation_id": row["causation_id"],
            "parent_event_id": row["parent_event_id"] or row["causation_id"],
            "root_event_id": row["root_event_id"] or row["id"],
            "causation_depth": row["causation_depth"] or 0,
            "replay_branch_id": row["replay_branch_id"] or "main",
            "timestamp": row["created_at"],
            "type": infer_workflow_event_type(str(row["agent"]), str(row["action"])).value,
            "severity": infer_workflow_event_severity(str(decision), str(row["action"]), confidence),
            "agent": row["agent"],
            "state": row["stage"],
            "action": row["action"],
            "decision": decision,
            "confidence": confidence,
            "reasons": reasons,
                "payload": {
                    "source_table": "workflow_events",
                    "reason_count": len(reasons),
                    "migration_audit": build_migration_audit("v1", {}),
                    "tenant_key": tenant_key,
                    **persisted_payload,
                },
            }
        )
    if not validate_event_compatibility(payload):
        payload["payload"]["compatibility_error"] = "event failed compatibility validation"
    return WorkflowEventRecord(
        **payload,
    )


def init_db() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                email_encrypted TEXT,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                org_unit TEXT,
                branch TEXT,
                doctor_name TEXT,
                specialty TEXT,
                specialization TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                availability TEXT NOT NULL DEFAULT 'Available',
                phone TEXT,
                phone_encrypted TEXT,
                email_verified_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
        user_alters = {
            "email_encrypted": "ALTER TABLE users ADD COLUMN email_encrypted TEXT",
            "tenant_key": "ALTER TABLE users ADD COLUMN tenant_key TEXT NOT NULL DEFAULT 'default-clinic'",
            "org_unit": "ALTER TABLE users ADD COLUMN org_unit TEXT",
            "branch": "ALTER TABLE users ADD COLUMN branch TEXT",
            "doctor_name": "ALTER TABLE users ADD COLUMN doctor_name TEXT",
            "specialty": "ALTER TABLE users ADD COLUMN specialty TEXT",
            "specialization": "ALTER TABLE users ADD COLUMN specialization TEXT",
            "status": "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
            "availability": "ALTER TABLE users ADD COLUMN availability TEXT NOT NULL DEFAULT 'Available'",
            "phone": "ALTER TABLE users ADD COLUMN phone TEXT",
            "phone_encrypted": "ALTER TABLE users ADD COLUMN phone_encrypted TEXT",
            "email_verified_at": "ALTER TABLE users ADD COLUMN email_verified_at TEXT",
        }
        for col, stmt in user_alters.items():
            if col not in user_columns:
                connection.execute(stmt)

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT NOT NULL UNIQUE,
                token_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'issued',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_name TEXT NOT NULL,
                patient_email TEXT,
                patient_email_encrypted TEXT,
                phone TEXT NOT NULL,
                phone_encrypted TEXT,
                patient_age INTEGER,
                medical_history TEXT,
                medical_history_encrypted TEXT,
                symptoms TEXT NOT NULL,
                extracted_symptoms TEXT,
                clinical_questionnaire_json TEXT,
                specialty TEXT NOT NULL,
                doctor_name TEXT NOT NULL,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                branch TEXT NOT NULL,
                appointment_date TEXT NOT NULL,
                slot_time TEXT,
                slot_id INTEGER,
                severity TEXT NOT NULL DEFAULT 'Low',
                urgency TEXT NOT NULL,
                confidence REAL NOT NULL,
                priority_score REAL NOT NULL DEFAULT 0,
                history_summary TEXT,
                quick_aid TEXT,
                triage_summary TEXT,
                doctor_selection_mode TEXT NOT NULL DEFAULT 'recommended',
                queue_state TEXT NOT NULL DEFAULT 'awaiting-doctor',
                status TEXT NOT NULL DEFAULT 'pending',
                created_by TEXT,
                acknowledged_at TEXT,
                acknowledged_by TEXT,
                follow_up_status TEXT NOT NULL DEFAULT 'none',
                reminder_sent INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS patient_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_name TEXT NOT NULL,
                patient_email TEXT,
                patient_email_encrypted TEXT,
                phone TEXT NOT NULL UNIQUE,
                phone_encrypted TEXT,
                patient_age INTEGER,
                chronic_conditions TEXT,
                chronic_conditions_encrypted TEXT,
                allergies TEXT,
                allergies_encrypted TEXT,
                gender TEXT,
                emergency_contact TEXT,
                emergency_contact_encrypted TEXT,
                communication_preferences_json TEXT,
                linked_user_id INTEGER,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                last_visit_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (linked_user_id) REFERENCES users(id)
            )
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(appointments)").fetchall()}
        alters = {
            "patient_email": "ALTER TABLE appointments ADD COLUMN patient_email TEXT",
            "patient_email_encrypted": "ALTER TABLE appointments ADD COLUMN patient_email_encrypted TEXT",
            "phone_encrypted": "ALTER TABLE appointments ADD COLUMN phone_encrypted TEXT",
            "patient_age": "ALTER TABLE appointments ADD COLUMN patient_age INTEGER",
            "medical_history": "ALTER TABLE appointments ADD COLUMN medical_history TEXT",
            "medical_history_encrypted": "ALTER TABLE appointments ADD COLUMN medical_history_encrypted TEXT",
            "extracted_symptoms": "ALTER TABLE appointments ADD COLUMN extracted_symptoms TEXT",
            "clinical_questionnaire_json": "ALTER TABLE appointments ADD COLUMN clinical_questionnaire_json TEXT",
            "tenant_key": "ALTER TABLE appointments ADD COLUMN tenant_key TEXT NOT NULL DEFAULT 'default-clinic'",
            "branch": "ALTER TABLE appointments ADD COLUMN branch TEXT NOT NULL DEFAULT 'Mysore Central'",
            "slot_time": "ALTER TABLE appointments ADD COLUMN slot_time TEXT",
            "slot_id": "ALTER TABLE appointments ADD COLUMN slot_id INTEGER",
            "severity": "ALTER TABLE appointments ADD COLUMN severity TEXT NOT NULL DEFAULT 'Low'",
            "status": "ALTER TABLE appointments ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'",
            "created_by": "ALTER TABLE appointments ADD COLUMN created_by TEXT",
            "acknowledged_at": "ALTER TABLE appointments ADD COLUMN acknowledged_at TEXT",
            "acknowledged_by": "ALTER TABLE appointments ADD COLUMN acknowledged_by TEXT",
            "follow_up_status": "ALTER TABLE appointments ADD COLUMN follow_up_status TEXT NOT NULL DEFAULT 'none'",
            "doctor_notes": "ALTER TABLE appointments ADD COLUMN doctor_notes TEXT",
            "cancel_reason": "ALTER TABLE appointments ADD COLUMN cancel_reason TEXT",
            "queue_state": "ALTER TABLE appointments ADD COLUMN queue_state TEXT NOT NULL DEFAULT 'awaiting-doctor'",
            "reminder_sent": "ALTER TABLE appointments ADD COLUMN reminder_sent INTEGER NOT NULL DEFAULT 0",
            "priority_score": "ALTER TABLE appointments ADD COLUMN priority_score REAL NOT NULL DEFAULT 0",
            "history_summary": "ALTER TABLE appointments ADD COLUMN history_summary TEXT",
            "quick_aid": "ALTER TABLE appointments ADD COLUMN quick_aid TEXT",
            "triage_summary": "ALTER TABLE appointments ADD COLUMN triage_summary TEXT",
            "doctor_selection_mode": "ALTER TABLE appointments ADD COLUMN doctor_selection_mode TEXT NOT NULL DEFAULT 'recommended'",
        }
        for col, stmt in alters.items():
            if col not in columns:
                connection.execute(stmt)
        profile_columns = {row["name"] for row in connection.execute("PRAGMA table_info(patient_profiles)").fetchall()}
        profile_alters = {
            "patient_email": "ALTER TABLE patient_profiles ADD COLUMN patient_email TEXT",
            "patient_email_encrypted": "ALTER TABLE patient_profiles ADD COLUMN patient_email_encrypted TEXT",
            "phone_encrypted": "ALTER TABLE patient_profiles ADD COLUMN phone_encrypted TEXT",
            "patient_age": "ALTER TABLE patient_profiles ADD COLUMN patient_age INTEGER",
            "chronic_conditions": "ALTER TABLE patient_profiles ADD COLUMN chronic_conditions TEXT",
            "chronic_conditions_encrypted": "ALTER TABLE patient_profiles ADD COLUMN chronic_conditions_encrypted TEXT",
            "allergies": "ALTER TABLE patient_profiles ADD COLUMN allergies TEXT",
            "allergies_encrypted": "ALTER TABLE patient_profiles ADD COLUMN allergies_encrypted TEXT",
            "gender": "ALTER TABLE patient_profiles ADD COLUMN gender TEXT",
            "emergency_contact": "ALTER TABLE patient_profiles ADD COLUMN emergency_contact TEXT",
            "emergency_contact_encrypted": "ALTER TABLE patient_profiles ADD COLUMN emergency_contact_encrypted TEXT",
            "communication_preferences_json": "ALTER TABLE patient_profiles ADD COLUMN communication_preferences_json TEXT",
            "linked_user_id": "ALTER TABLE patient_profiles ADD COLUMN linked_user_id INTEGER",
            "tenant_key": "ALTER TABLE patient_profiles ADD COLUMN tenant_key TEXT NOT NULL DEFAULT 'default-clinic'",
            "last_visit_at": "ALTER TABLE patient_profiles ADD COLUMN last_visit_at TEXT",
            "created_at": "ALTER TABLE patient_profiles ADD COLUMN created_at TEXT",
            "updated_at": "ALTER TABLE patient_profiles ADD COLUMN updated_at TEXT",
        }
        for col, stmt in profile_alters.items():
            if col not in profile_columns:
                connection.execute(stmt)

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                target_type TEXT NOT NULL,
                target_name TEXT NOT NULL,
                channel TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL,
                external_id TEXT,
                correlation_id TEXT,
                acknowledged_at TEXT,
                provider_metadata_json TEXT,
                message_category TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (appointment_id) REFERENCES appointments(id)
            )
            """
        )
        notification_columns = {row["name"] for row in connection.execute("PRAGMA table_info(notifications)").fetchall()}
        notification_alters = {
            "tenant_key": "ALTER TABLE notifications ADD COLUMN tenant_key TEXT NOT NULL DEFAULT 'default-clinic'",
            "external_id": "ALTER TABLE notifications ADD COLUMN external_id TEXT",
            "attempt_count": "ALTER TABLE notifications ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
            "next_attempt_at": "ALTER TABLE notifications ADD COLUMN next_attempt_at TEXT",
            "last_error": "ALTER TABLE notifications ADD COLUMN last_error TEXT",
            "correlation_id": "ALTER TABLE notifications ADD COLUMN correlation_id TEXT",
            "acknowledged_at": "ALTER TABLE notifications ADD COLUMN acknowledged_at TEXT",
            "provider_metadata_json": "ALTER TABLE notifications ADD COLUMN provider_metadata_json TEXT",
            "message_category": "ALTER TABLE notifications ADD COLUMN message_category TEXT",
        }
        for col, stmt in notification_alters.items():
            if col not in notification_columns:
                connection.execute(stmt)

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS clinical_diaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER NOT NULL,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                doctor_name TEXT NOT NULL,
                author_name TEXT NOT NULL,
                diary_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (appointment_id) REFERENCES appointments(id)
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS prescriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER NOT NULL,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                doctor_name TEXT NOT NULL,
                patient_name TEXT NOT NULL,
                author_name TEXT NOT NULL,
                prescription_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'issued',
                delivered_via TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (appointment_id) REFERENCES appointments(id)
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS patient_vitals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                patient_name TEXT,
                patient_email TEXT,
                phone TEXT,
                blood_pressure TEXT,
                systolic_bp REAL,
                diastolic_bp REAL,
                heart_rate REAL,
                respiratory_rate REAL,
                spo2 REAL,
                temperature_f REAL,
                height_cm REAL,
                weight_kg REAL,
                risk_level TEXT NOT NULL DEFAULT 'normal',
                abnormal_flags_json TEXT,
                recorded_at TEXT NOT NULL,
                FOREIGN KEY (appointment_id) REFERENCES appointments(id)
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS emergency_contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_profile_id INTEGER,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                contact_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                relationship TEXT,
                consent_status TEXT NOT NULL DEFAULT 'pending',
                notification_status TEXT NOT NULL DEFAULT 'not_notified',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (patient_profile_id) REFERENCES patient_profiles(id)
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS emergency_escalations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                workflow_id TEXT NOT NULL,
                patient_name TEXT,
                patient_phone TEXT,
                patient_email TEXT,
                risk_level TEXT NOT NULL,
                risk_score REAL NOT NULL DEFAULT 0,
                summary TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (appointment_id) REFERENCES appointments(id)
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS report_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                patient_name TEXT,
                report_type TEXT NOT NULL DEFAULT 'unknown',
                file_name TEXT,
                ocr_status TEXT NOT NULL DEFAULT 'pending',
                review_status TEXT NOT NULL DEFAULT 'pending',
                review_notes TEXT,
                lab_values_json TEXT,
                abnormal_findings_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (appointment_id) REFERENCES appointments(id)
            )
            """
        )
        report_columns = {row["name"] for row in connection.execute("PRAGMA table_info(report_analyses)").fetchall()}
        report_alters = {
            "review_status": "ALTER TABLE report_analyses ADD COLUMN review_status TEXT NOT NULL DEFAULT 'pending'",
            "review_notes": "ALTER TABLE report_analyses ADD COLUMN review_notes TEXT",
        }
        for col, stmt in report_alters.items():
            if col not in report_columns:
                connection.execute(stmt)

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS care_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER NOT NULL,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                doctor_name TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                approval_status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (appointment_id) REFERENCES appointments(id)
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS monitoring_checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                patient_name TEXT,
                prompt TEXT NOT NULL,
                response_text TEXT,
                recovery_score REAL,
                risk_reassessment_json TEXT,
                status TEXT NOT NULL DEFAULT 'scheduled',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (appointment_id) REFERENCES appointments(id)
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS doctor_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doctor_name TEXT NOT NULL,
                specialty TEXT NOT NULL,
                branch TEXT NOT NULL,
                slot_date TEXT NOT NULL,
                slot_time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'available',
                appointment_id INTEGER,
                UNIQUE(doctor_name, slot_date, slot_time)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS doctor_schedule_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doctor_name TEXT NOT NULL UNIQUE,
                specialty TEXT NOT NULL,
                branch TEXT NOT NULL,
                working_hours_json TEXT NOT NULL,
                slot_interval_minutes INTEGER NOT NULL DEFAULT 30,
                status TEXT NOT NULL DEFAULT 'active',
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS doctor_unavailability (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doctor_name TEXT NOT NULL,
                unavailable_date TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                UNIQUE(doctor_name, unavailable_date, reason)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_name TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id INTEGER,
                details TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS automation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_name TEXT NOT NULL,
                status TEXT NOT NULL,
                details TEXT,
                processed_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                trace_id TEXT,
                correlation_id TEXT,
                causation_id INTEGER,
                parent_event_id INTEGER,
                root_event_id INTEGER,
                causation_depth INTEGER NOT NULL DEFAULT 0,
                replay_branch_id TEXT NOT NULL DEFAULT 'main',
                stage TEXT NOT NULL,
                agent TEXT NOT NULL,
                action TEXT NOT NULL,
                decision TEXT,
                confidence REAL,
                reasons TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        workflow_columns = {row["name"] for row in connection.execute("PRAGMA table_info(workflow_events)").fetchall()}
        workflow_alters = {
            "tenant_key": "ALTER TABLE workflow_events ADD COLUMN tenant_key TEXT NOT NULL DEFAULT 'default-clinic'",
            "trace_id": "ALTER TABLE workflow_events ADD COLUMN trace_id TEXT",
            "correlation_id": "ALTER TABLE workflow_events ADD COLUMN correlation_id TEXT",
            "causation_id": "ALTER TABLE workflow_events ADD COLUMN causation_id INTEGER",
            "parent_event_id": "ALTER TABLE workflow_events ADD COLUMN parent_event_id INTEGER",
            "root_event_id": "ALTER TABLE workflow_events ADD COLUMN root_event_id INTEGER",
            "causation_depth": "ALTER TABLE workflow_events ADD COLUMN causation_depth INTEGER NOT NULL DEFAULT 0",
            "replay_branch_id": "ALTER TABLE workflow_events ADD COLUMN replay_branch_id TEXT NOT NULL DEFAULT 'main'",
            "payload_json": "ALTER TABLE workflow_events ADD COLUMN payload_json TEXT",
            "event_fingerprint": "ALTER TABLE workflow_events ADD COLUMN event_fingerprint TEXT",
        }
        for col, stmt in workflow_alters.items():
            if col not in workflow_columns:
                connection.execute(stmt)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_execution_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invocation_id TEXT NOT NULL UNIQUE,
                workflow_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                agent TEXT NOT NULL,
                parent_event_id INTEGER,
                replay_branch_id TEXT NOT NULL DEFAULT 'main',
                latency_ms INTEGER NOT NULL DEFAULT 0,
                success INTEGER NOT NULL DEFAULT 1,
                fallback_used INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        tool_columns = {row["name"] for row in connection.execute("PRAGMA table_info(tool_execution_logs)").fetchall()}
        tool_alters = {
            "parent_event_id": "ALTER TABLE tool_execution_logs ADD COLUMN parent_event_id INTEGER",
            "replay_branch_id": "ALTER TABLE tool_execution_logs ADD COLUMN replay_branch_id TEXT NOT NULL DEFAULT 'main'",
        }
        for col, stmt in tool_alters.items():
            if col not in tool_columns:
                connection.execute(stmt)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_feature_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                patient_id TEXT,
                conversation_id TEXT NOT NULL,
                model_family TEXT NOT NULL,
                feature_version TEXT NOT NULL,
                feature_snapshot_hash TEXT NOT NULL,
                model_input_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                symptom_text TEXT NOT NULL,
                structured_features_json TEXT NOT NULL,
                temporal_features_json TEXT NOT NULL,
                text_features_hash TEXT NOT NULL,
                label_status TEXT NOT NULL DEFAULT 'unlabeled',
                label_source TEXT,
                label_updated_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_model_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_key TEXT NOT NULL UNIQUE,
                model_family TEXT NOT NULL,
                feature_version TEXT NOT NULL,
                training_dataset_version TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                calibration_artifact_path TEXT,
                metrics_json TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                promoted_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_threshold_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_key TEXT NOT NULL UNIQUE,
                thresholds_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                promoted_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                feature_snapshot_id INTEGER NOT NULL,
                model_registry_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                raw_score REAL NOT NULL,
                calibrated_score REAL NOT NULL,
                risk_band TEXT NOT NULL,
                predicted_specialty TEXT NOT NULL,
                predicted_urgency TEXT NOT NULL,
                predicted_severity TEXT NOT NULL,
                requires_review INTEGER NOT NULL DEFAULT 0,
                threshold_profile_id INTEGER NOT NULL,
                feature_snapshot_hash TEXT NOT NULL,
                model_input_hash TEXT NOT NULL,
                model_key TEXT NOT NULL,
                model_version TEXT NOT NULL,
                feature_version TEXT NOT NULL,
                active_model_key TEXT,
                candidate_model_key TEXT,
                explanations_json TEXT,
                top_features_json TEXT,
                is_shadow_prediction INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (feature_snapshot_id) REFERENCES risk_feature_snapshots(id),
                FOREIGN KEY (model_registry_id) REFERENCES risk_model_registry(id),
                FOREIGN KEY (threshold_profile_id) REFERENCES risk_threshold_profiles(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS promotion_gate_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_key TEXT NOT NULL UNIQUE,
                gate_rules_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS model_evaluation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                evaluation_run_key TEXT NOT NULL UNIQUE,
                candidate_model_registry_id INTEGER NOT NULL,
                active_model_registry_id INTEGER NOT NULL,
                candidate_threshold_profile_id INTEGER NOT NULL,
                active_threshold_profile_id INTEGER NOT NULL,
                evaluation_scope TEXT NOT NULL,
                workflow_count INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                replay_integrity_passed INTEGER NOT NULL DEFAULT 1,
                evaluation_checksum TEXT,
                summary_json TEXT,
                promotion_recommendation TEXT,
                promotion_gate_result TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS model_evaluation_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                evaluation_run_id INTEGER NOT NULL,
                workflow_id TEXT NOT NULL,
                feature_snapshot_id INTEGER NOT NULL,
                replay_integrity_status TEXT NOT NULL,
                active_prediction_id INTEGER NOT NULL,
                candidate_prediction_id INTEGER NOT NULL,
                active_policy_path TEXT NOT NULL,
                candidate_policy_path TEXT NOT NULL,
                escalation_delta INTEGER NOT NULL DEFAULT 0,
                review_delta INTEGER NOT NULL DEFAULT 0,
                threshold_delta TEXT,
                severity_delta TEXT,
                specialty_delta TEXT,
                calibration_delta REAL NOT NULL DEFAULT 0,
                false_negative_risk INTEGER NOT NULL DEFAULT 0,
                divergence_summary_json TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS evaluation_drift_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                evaluation_run_id INTEGER NOT NULL,
                score_distribution_delta REAL NOT NULL DEFAULT 0,
                specialty_distribution_delta REAL NOT NULL DEFAULT 0,
                review_rate_delta REAL NOT NULL DEFAULT 0,
                escalation_delta REAL NOT NULL DEFAULT 0,
                false_negative_delta REAL NOT NULL DEFAULT 0,
                calibration_error_delta REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS governance_recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recommendation_key TEXT NOT NULL UNIQUE,
                recommendation_type TEXT NOT NULL,
                source_evaluation_run_id INTEGER,
                candidate_model_registry_id INTEGER,
                threshold_profile_id INTEGER,
                recommendation_status TEXT NOT NULL,
                recommendation_reason TEXT NOT NULL,
                confidence_score REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                supporting_evidence_json TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS governance_rollout_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rollout_profile_key TEXT NOT NULL UNIQUE,
                rollout_percentages_json TEXT NOT NULL,
                safety_constraints_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS governance_timelines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                governance_entity_type TEXT NOT NULL,
                governance_entity_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_timestamp TEXT NOT NULL,
                related_model_key TEXT,
                related_threshold_profile_key TEXT,
                incident_correlation_id TEXT,
                payload_json TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS drift_trigger_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_key TEXT NOT NULL UNIQUE,
                drift_metric_type TEXT NOT NULL,
                threshold_value REAL NOT NULL,
                trigger_action TEXT NOT NULL,
                cooldown_minutes INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS worker_execution_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL UNIQUE,
                task_name TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                originating_event_id INTEGER,
                idempotency_key TEXT NOT NULL UNIQUE,
                execution_checksum TEXT NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                execution_state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                payload_json TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS replay_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                replay_branch_id TEXT NOT NULL DEFAULT 'main',
                snapshot_event_id INTEGER NOT NULL,
                snapshot_checksum TEXT NOT NULL,
                workflow_state_blob TEXT NOT NULL,
                lineage_metadata TEXT NOT NULL,
                created_at TEXT NOT NULL,
                invalidated_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS worker_leases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_id TEXT NOT NULL,
                task_id TEXT NOT NULL UNIQUE,
                workflow_id TEXT NOT NULL,
                lease_token TEXT NOT NULL UNIQUE,
                lease_expiration TEXT NOT NULL,
                retry_generation INTEGER NOT NULL DEFAULT 0,
                execution_checksum TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS advisory_locks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lock_key TEXT NOT NULL UNIQUE,
                owner_id TEXT NOT NULL,
                lock_token TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL UNIQUE,
                worker_generation INTEGER NOT NULL DEFAULT 0,
                stream_generation INTEGER NOT NULL DEFAULT 0,
                replay_generation INTEGER NOT NULL DEFAULT 0,
                lease_generation INTEGER NOT NULL DEFAULT 0,
                heartbeat_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS consumer_ownership (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                consumer_id TEXT NOT NULL UNIQUE,
                node_id TEXT NOT NULL,
                stream_subject TEXT NOT NULL,
                lease_token TEXT,
                ownership_generation INTEGER NOT NULL DEFAULT 0,
                checkpoint_outbox_id INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS intelligence_rollups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rollup_key TEXT NOT NULL UNIQUE,
                rollup_type TEXT NOT NULL,
                rollup_scope TEXT NOT NULL DEFAULT 'global',
                source_checksum TEXT NOT NULL,
                rollup_checksum TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rollup_generation_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rollup_key TEXT NOT NULL,
                generation_status TEXT NOT NULL,
                workflow_count INTEGER NOT NULL DEFAULT 0,
                rollup_checksum TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS partition_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL UNIQUE,
                partition_strategy TEXT NOT NULL,
                retention_days INTEGER NOT NULL DEFAULT 90,
                archive_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS event_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL UNIQUE,
                schema_version TEXT NOT NULL,
                event_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                replay_branch_id TEXT NOT NULL DEFAULT 'main',
                trace_id TEXT NOT NULL,
                root_event_id INTEGER,
                causation_id INTEGER,
                payload_checksum TEXT NOT NULL,
                publish_generation INTEGER NOT NULL DEFAULT 0,
                publish_status TEXT NOT NULL DEFAULT 'pending',
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                published_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS event_delivery_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                outbox_id INTEGER NOT NULL,
                consumer_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                processing_checksum TEXT NOT NULL,
                delivery_status TEXT NOT NULL DEFAULT 'processed',
                retry_generation INTEGER NOT NULL DEFAULT 0,
                lease_token TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                UNIQUE(outbox_id, consumer_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS consumer_checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                consumer_id TEXT NOT NULL UNIQUE,
                last_outbox_id INTEGER NOT NULL DEFAULT 0,
                last_event_id INTEGER NOT NULL DEFAULT 0,
                checkpoint_checksum TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS projection_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                projection_name TEXT NOT NULL,
                projection_scope TEXT NOT NULL DEFAULT 'global',
                payload_json TEXT NOT NULL,
                projection_checksum TEXT NOT NULL,
                source_outbox_id INTEGER NOT NULL DEFAULT 0,
                source_event_id INTEGER NOT NULL DEFAULT 0,
                projection_generation INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(projection_name, projection_scope)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS projection_checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                projection_name TEXT NOT NULL,
                projection_scope TEXT NOT NULL DEFAULT 'global',
                source_outbox_id INTEGER NOT NULL DEFAULT 0,
                source_event_id INTEGER NOT NULL DEFAULT 0,
                projection_generation INTEGER NOT NULL DEFAULT 0,
                projection_checksum TEXT NOT NULL,
                replay_lineage_metadata TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(projection_name, projection_scope)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS appointment_lifecycle_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER NOT NULL,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                workflow_id TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT NOT NULL,
                cause TEXT NOT NULL,
                responsible_actor TEXT NOT NULL,
                responsible_role TEXT NOT NULL,
                event_id INTEGER,
                sla_due_at TEXT,
                escalation_lineage TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sla_violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                workflow_id TEXT NOT NULL,
                sla_type TEXT NOT NULL,
                threshold_minutes INTEGER NOT NULL,
                observed_minutes INTEGER NOT NULL,
                action_triggered TEXT NOT NULL,
                violation_status TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS coordination_queue_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_type TEXT NOT NULL,
                appointment_id INTEGER,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                workflow_id TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                queue_status TEXT NOT NULL,
                assigned_owner TEXT,
                causation_lineage TEXT NOT NULL DEFAULT '{}',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS calendar_sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER NOT NULL,
                tenant_key TEXT NOT NULL DEFAULT 'default-clinic',
                provider TEXT NOT NULL,
                sync_direction TEXT NOT NULL,
                sync_status TEXT NOT NULL,
                external_ref TEXT,
                conflict_detected INTEGER NOT NULL DEFAULT 0,
                retry_count INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_key TEXT NOT NULL UNIQUE,
                tenant_name TEXT NOT NULL,
                tenant_type TEXT NOT NULL,
                parent_tenant_key TEXT,
                status TEXT NOT NULL,
                encryption_context TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tenant_memberships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tenant_key TEXT NOT NULL,
                role_scope TEXT NOT NULL,
                org_unit TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, tenant_key, role_scope)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS compliance_access_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER,
                actor_email TEXT,
                tenant_key TEXT NOT NULL,
                access_type TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                masked_fields_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_key TEXT NOT NULL,
                export_type TEXT NOT NULL,
                checksum TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS integration_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_key TEXT NOT NULL,
                tenant_key TEXT NOT NULL,
                status TEXT NOT NULL,
                last_error TEXT,
                last_checked_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                UNIQUE(provider_key, tenant_key)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS backup_exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_key TEXT NOT NULL,
                backup_type TEXT NOT NULL,
                checksum TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS billing_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_key TEXT NOT NULL,
                appointment_id INTEGER,
                workflow_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                amount_cents INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        risk_prediction_columns = {row["name"] for row in connection.execute("PRAGMA table_info(risk_predictions)").fetchall()}
        risk_prediction_alters = {
            "feature_snapshot_hash": "ALTER TABLE risk_predictions ADD COLUMN feature_snapshot_hash TEXT",
            "model_input_hash": "ALTER TABLE risk_predictions ADD COLUMN model_input_hash TEXT",
            "model_key": "ALTER TABLE risk_predictions ADD COLUMN model_key TEXT",
            "model_version": "ALTER TABLE risk_predictions ADD COLUMN model_version TEXT",
            "feature_version": "ALTER TABLE risk_predictions ADD COLUMN feature_version TEXT",
            "active_model_key": "ALTER TABLE risk_predictions ADD COLUMN active_model_key TEXT",
            "candidate_model_key": "ALTER TABLE risk_predictions ADD COLUMN candidate_model_key TEXT",
        }
        for col, stmt in risk_prediction_alters.items():
            if col not in risk_prediction_columns:
                connection.execute(stmt)
        worker_execution_columns = {row["name"] for row in connection.execute("PRAGMA table_info(worker_execution_ledger)").fetchall()}
        worker_execution_alters = {
            "updated_at": "ALTER TABLE worker_execution_ledger ADD COLUMN updated_at TEXT",
            "payload_json": "ALTER TABLE worker_execution_ledger ADD COLUMN payload_json TEXT",
            "execution_generation": "ALTER TABLE worker_execution_ledger ADD COLUMN execution_generation INTEGER NOT NULL DEFAULT 0",
            "owner_worker_id": "ALTER TABLE worker_execution_ledger ADD COLUMN owner_worker_id TEXT",
            "lease_token": "ALTER TABLE worker_execution_ledger ADD COLUMN lease_token TEXT",
        }
        for col, stmt in worker_execution_alters.items():
            if col not in worker_execution_columns:
                connection.execute(stmt)
        immutable_tables = [
            "tool_execution_logs",
            "risk_feature_snapshots",
            "risk_predictions",
            "model_evaluation_results",
            "evaluation_drift_snapshots",
            "governance_timelines",
            "replay_snapshots",
            "intelligence_rollups",
            "rollup_generation_metadata",
            "appointment_lifecycle_transitions",
            "sla_violations",
            "calendar_sync_runs",
        ]
        for table_name in immutable_tables:
            connection.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS prevent_delete_{table_name}
                BEFORE DELETE ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, 'append-only table: delete blocked');
                END;
                """
            )
            connection.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS prevent_update_{table_name}
                BEFORE UPDATE ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, 'append-only table: update blocked');
                END;
                """
            )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_invalid_update_workflow_events
            BEFORE UPDATE ON workflow_events
            WHEN NOT (
                OLD.root_event_id IS NULL
                AND NEW.root_event_id = OLD.id
                AND OLD.workflow_id = NEW.workflow_id
                AND COALESCE(OLD.trace_id, '') = COALESCE(NEW.trace_id, '')
                AND COALESCE(OLD.correlation_id, '') = COALESCE(NEW.correlation_id, '')
                AND COALESCE(OLD.causation_id, -1) = COALESCE(NEW.causation_id, -1)
                AND COALESCE(OLD.parent_event_id, -1) = COALESCE(NEW.parent_event_id, -1)
                AND COALESCE(OLD.causation_depth, 0) = COALESCE(NEW.causation_depth, 0)
                AND COALESCE(OLD.replay_branch_id, '') = COALESCE(NEW.replay_branch_id, '')
                AND OLD.stage = NEW.stage
                AND OLD.agent = NEW.agent
                AND OLD.action = NEW.action
                AND COALESCE(OLD.decision, '') = COALESCE(NEW.decision, '')
                AND COALESCE(OLD.confidence, -1) = COALESCE(NEW.confidence, -1)
                AND COALESCE(OLD.reasons, '') = COALESCE(NEW.reasons, '')
                AND COALESCE(OLD.payload_json, '') = COALESCE(NEW.payload_json, '')
                AND COALESCE(OLD.event_fingerprint, '') = COALESCE(NEW.event_fingerprint, '')
                AND OLD.created_at = NEW.created_at
            )
            BEGIN
                SELECT RAISE(ABORT, 'append-only table: workflow_events update blocked');
            END;
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_events_fingerprint
            ON workflow_events (event_fingerprint)
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_workflow_events_workflow_id ON workflow_events (workflow_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_workflow_events_trace_id ON workflow_events (trace_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_workflow_events_root_event_id ON workflow_events (root_event_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_workflow_events_causation_id ON workflow_events (causation_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_workflow_events_replay_branch_id ON workflow_events (replay_branch_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_workflow_events_created_at ON workflow_events (created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_workflow_events_decision ON workflow_events (decision)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_tool_execution_logs_workflow_id ON tool_execution_logs (workflow_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_tool_execution_logs_parent_event_id ON tool_execution_logs (parent_event_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_risk_predictions_workflow_id ON risk_predictions (workflow_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_risk_predictions_model_registry_id ON risk_predictions (model_registry_id)")
        connection.execute("DROP INDEX IF EXISTS idx_model_evaluation_runs_checksum")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_model_evaluation_runs_checksum ON model_evaluation_runs (evaluation_checksum)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_model_evaluation_results_run_id ON model_evaluation_results (evaluation_run_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_governance_recommendations_run_id ON governance_recommendations (source_evaluation_run_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_governance_timelines_entity ON governance_timelines (governance_entity_type, governance_entity_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_worker_execution_ledger_workflow_id ON worker_execution_ledger (workflow_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_worker_execution_ledger_state ON worker_execution_ledger (execution_state)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_replay_snapshots_workflow_branch ON replay_snapshots (workflow_id, replay_branch_id, snapshot_event_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_worker_leases_task_id ON worker_leases (task_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_worker_leases_expiration ON worker_leases (lease_expiration)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_advisory_locks_expires_at ON advisory_locks (expires_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_runtime_nodes_status ON runtime_nodes (status, heartbeat_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_consumer_ownership_node_subject ON consumer_ownership (node_id, stream_subject)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_intelligence_rollups_type_scope ON intelligence_rollups (rollup_type, rollup_scope, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_event_outbox_status_id ON event_outbox (publish_status, id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_event_outbox_workflow_id ON event_outbox (workflow_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_event_delivery_records_consumer ON event_delivery_records (consumer_id, outbox_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_projection_snapshots_name_scope ON projection_snapshots (projection_name, projection_scope)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_appointment_lifecycle_transitions_appointment_id ON appointment_lifecycle_transitions (appointment_id, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sla_violations_workflow_id ON sla_violations (workflow_id, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_coordination_queue_items_queue_type ON coordination_queue_items (queue_type, queue_status, priority)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_calendar_sync_runs_appointment_id ON calendar_sync_runs (appointment_id, provider, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_users_tenant_key ON users (tenant_key)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_appointments_tenant_key ON appointments (tenant_key, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_workflow_events_tenant_key ON workflow_events (tenant_key, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_notifications_tenant_key ON notifications (tenant_key, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_patient_profiles_tenant_key ON patient_profiles (tenant_key, updated_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_auth_tokens_lookup ON auth_tokens (token_type, status, expires_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_notifications_correlation_id ON notifications (correlation_id, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_tenant_memberships_tenant_key ON tenant_memberships (tenant_key, role_scope)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_compliance_access_logs_tenant_key ON compliance_access_logs (tenant_key, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_audit_exports_tenant_key ON audit_exports (tenant_key, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_backup_exports_tenant_key ON backup_exports (tenant_key, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_billing_events_tenant_key ON billing_events (tenant_key, created_at)")
        _ensure_default_risk_governance(connection)


def _ensure_default_risk_governance(connection: sqlite3.Connection) -> None:
    now = dt.datetime.now().isoformat(timespec="seconds")
    connection.execute(
        """
        INSERT OR IGNORE INTO tenants (
            tenant_key, tenant_name, tenant_type, parent_tenant_key, status, encryption_context, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("default-clinic", "DOCQ Default Clinic", "clinic", None, "active", json.dumps({"scope": "default-clinic"}, sort_keys=True), now),
    )
    threshold_defaults = [
        (
            "risk-threshold-active",
            {"medium": 0.45, "high": 0.72, "emergency": 0.9, "review_confidence_lt": 75.0},
            "active",
            now,
            now,
        ),
        (
            "risk-threshold-candidate",
            {"medium": 0.4, "high": 0.68, "emergency": 0.88, "review_confidence_lt": 78.0},
            "candidate",
            now,
            None,
        ),
    ]
    for profile_key, thresholds, status, created_at, promoted_at in threshold_defaults:
        connection.execute(
            """
            INSERT OR IGNORE INTO risk_threshold_profiles (
                profile_key, thresholds_json, status, created_at, promoted_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (profile_key, json.dumps(thresholds, sort_keys=True), status, created_at, promoted_at),
        )
    model_defaults = [
        (
            "risk-active",
            "sklearn-logreg",
            "ml-v2-structured-v1",
            "dataset-v1",
            "models/category_model.pkl",
            "models/category_model.pkl",
            json.dumps({"status": "bootstrap", "shadow": False}, sort_keys=True),
            "active",
            now,
            now,
        ),
        (
            "risk-candidate",
            "sklearn-logreg",
            "ml-v2-structured-v1",
            "dataset-v2-candidate",
            "models/category_model.pkl",
            "models/category_model.pkl",
            json.dumps({"status": "shadow", "shadow": True}, sort_keys=True),
            "candidate",
            now,
            None,
        ),
    ]
    for model_key, model_family, feature_version, training_dataset_version, artifact_path, calibration_artifact_path, metrics_json, status, created_at, promoted_at in model_defaults:
        connection.execute(
            """
            INSERT OR IGNORE INTO risk_model_registry (
                model_key, model_family, feature_version, training_dataset_version, artifact_path,
                calibration_artifact_path, metrics_json, status, created_at, promoted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_key,
                model_family,
                feature_version,
                training_dataset_version,
                artifact_path,
                calibration_artifact_path,
                metrics_json,
                status,
                created_at,
                promoted_at,
            ),
        )
    gate_defaults = [
        (
            "promotion-default",
            {
                "require_replay_integrity": True,
                "max_false_negative_delta": 0.0,
                "max_review_rate_delta": 10.0,
                "max_escalation_delta": 10.0,
                "min_calibration_improvement": -5.0,
            },
            "active",
            now,
        )
    ]
    for profile_key, rules, status, created_at in gate_defaults:
        connection.execute(
            """
            INSERT OR IGNORE INTO promotion_gate_profiles (profile_key, gate_rules_json, status, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (profile_key, json.dumps(rules, sort_keys=True), status, created_at),
        )
    rollout_defaults = [
        (
            "rollout-default",
            [10, 25, 50, 100],
            {
                "max_false_negative_delta": 0.0,
                "max_review_rate_delta": 10.0,
                "max_escalation_delta": 10.0,
            },
            "active",
            now,
        )
    ]
    for profile_key, percentages, constraints, status, created_at in rollout_defaults:
        connection.execute(
            """
            INSERT OR IGNORE INTO governance_rollout_profiles (rollout_profile_key, rollout_percentages_json, safety_constraints_json, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (profile_key, json.dumps(percentages), json.dumps(constraints, sort_keys=True), status, created_at),
        )
    drift_rule_defaults = [
        ("drift-review-rate", "review_rate_drift", 10.0, "launch_evaluation", 0, "active", now),
        ("drift-calibration", "calibration_error_drift", 5.0, "launch_evaluation", 0, "active", now),
        ("drift-false-negative", "emergency_false_negative_drift", 0.0, "launch_evaluation", 0, "active", now),
    ]
    for rule_key, metric_type, threshold_value, trigger_action, cooldown_minutes, status, created_at in drift_rule_defaults:
        connection.execute(
            """
            INSERT OR IGNORE INTO drift_trigger_rules (rule_key, drift_metric_type, threshold_value, trigger_action, cooldown_minutes, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (rule_key, metric_type, threshold_value, trigger_action, cooldown_minutes, status, created_at),
        )
    partition_defaults = [
        ("workflow_events", "monthly", 180, 1, now),
        ("tool_execution_logs", "monthly", 120, 1, now),
        ("governance_timelines", "monthly", 365, 1, now),
        ("model_evaluation_results", "monthly", 365, 1, now),
        ("worker_execution_ledger", "monthly", 90, 1, now),
        ("event_outbox", "monthly", 45, 1, now),
        ("event_delivery_records", "monthly", 45, 1, now),
        ("appointment_lifecycle_transitions", "monthly", 365, 1, now),
        ("sla_violations", "monthly", 180, 1, now),
        ("calendar_sync_runs", "monthly", 180, 1, now),
        ("audit_exports", "monthly", 365, 1, now),
        ("backup_exports", "monthly", 365, 1, now),
        ("billing_events", "monthly", 365, 1, now),
    ]
    for table_name, strategy, retention_days, archive_enabled, created_at in partition_defaults:
        connection.execute(
            """
            INSERT OR IGNORE INTO partition_metadata (table_name, partition_strategy, retention_days, archive_enabled, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (table_name, strategy, retention_days, archive_enabled, created_at),
        )


def _parse_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def seed_slots(enabled: bool, days: int = 5) -> None:
    if not enabled:
        return
    ensure_scheduling_tables()
    sync_default_doctor_schedules([dict(doctor) for doctor in DOCTOR_ACCOUNTS])
    today = get_current_date()
    with get_connection() as connection:
        for doctor in DOCTOR_ACCOUNTS:
            for offset in range(days):
                slot_date = (today + dt.timedelta(days=offset)).isoformat()
                for slot_time in DEFAULT_SLOT_TIMES:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO doctor_slots (doctor_name, specialty, branch, slot_date, slot_time, status)
                        VALUES (?, ?, ?, ?, ?, 'available')
                        """,
                        (doctor["doctor_name"], doctor["specialty"], doctor["branch"], slot_date, slot_time),
                    )


def seed_slots_for_doctor(doctor_name: str, specialty: str, branch: str, days: int = 14) -> None:
    ensure_scheduling_tables()
    sync_default_doctor_schedules([{"doctor_name": doctor_name, "specialty": specialty, "branch": branch}])
    today = get_current_date()
    with get_connection() as connection:
        for offset in range(days):
            slot_date = (today + dt.timedelta(days=offset)).isoformat()
            for slot_time in DEFAULT_SLOT_TIMES:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO doctor_slots (doctor_name, specialty, branch, slot_date, slot_time, status)
                    VALUES (?, ?, ?, ?, ?, 'available')
                    """,
                    (doctor_name, specialty, branch, slot_date, slot_time),
                )


def fetch_doctor_users(include_inactive: bool = False) -> list[sqlite3.Row]:
    query = """
        SELECT id, name, email, role, tenant_key, org_unit, branch, doctor_name, specialty,
               COALESCE(specialization, '') AS specialization,
               COALESCE(status, 'active') AS status,
               COALESCE(availability, 'Available') AS availability,
               phone, created_at
        FROM users
        WHERE role IN ('doctor', 'clinician')
    """
    params: list[object] = []
    if not include_inactive:
        query += " AND COALESCE(status, 'active') = 'active'"
    query += " ORDER BY specialty ASC, name ASC"
    with get_connection() as connection:
        return connection.execute(query, params).fetchall()


def update_doctor_user(
    user_id: int,
    *,
    name: str | None = None,
    department: str | None = None,
    branch: str | None = None,
    specialization: str | None = None,
    status: str | None = None,
    availability: str | None = None,
) -> None:
    fields = []
    values: list[object] = []
    if name is not None:
        fields.append("name = ?")
        values.append(name.strip())
    if department is not None:
        specialty = normalize_specialty(department)
        fields.extend(["specialty = ?", "org_unit = ?"])
        values.extend([specialty, specialty])
    if branch is not None:
        fields.append("branch = ?")
        values.append(branch.strip())
    if specialization is not None:
        fields.append("specialization = ?")
        values.append(specialization.strip())
    if status is not None:
        fields.append("status = ?")
        values.append(status.strip() or "active")
    if availability is not None:
        fields.append("availability = ?")
        values.append(availability.strip() or "Available")
    if not fields:
        return
    values.append(int(user_id))
    with get_connection() as connection:
        connection.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ? AND role IN ('doctor', 'clinician')", values)


def log_action(actor_name: str, actor_role: str, action: str, entity_type: str, entity_id: int | None, details: str) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO audit_logs (actor_name, actor_role, action, entity_type, entity_id, details, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (actor_name, actor_role, action, entity_type, entity_id, details, dt.datetime.now().isoformat(timespec="seconds")),
        )


def record_workflow_event(
    workflow_id: str,
    *,
    trace_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: int | None = None,
    parent_event_id: int | None = None,
    root_event_id: int | None = None,
    causation_depth: int = 0,
    replay_branch_id: str = "main",
    stage: str,
    agent: str,
    action: str,
    decision: str = "",
    confidence: float | None = None,
    reasons: list[str] | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    enriched_payload = dict(payload or {})
    tenant_key = str(enriched_payload.get("tenant_key") or get_current_tenant_key())
    enriched_payload.setdefault("tenant_key", tenant_key)
    return workflow_event_repository.append_event(
        tenant_key=tenant_key,
        workflow_id=workflow_id,
        trace_id=trace_id or workflow_id,
        correlation_id=correlation_id or workflow_id,
        causation_id=causation_id,
        parent_event_id=parent_event_id or causation_id,
        root_event_id=root_event_id,
        causation_depth=causation_depth,
        replay_branch_id=replay_branch_id,
        stage=stage,
        agent=agent,
        action=action,
        event_type=infer_workflow_event_type(agent, action).value,
        decision=decision,
        confidence=confidence,
        reasons=reasons or [],
        payload=enriched_payload,
    )


def record_tool_execution(telemetry: dict[str, object]) -> None:
    normalized = ToolExecutionTelemetry(**telemetry)
    telemetry_repository.record_tool_execution(normalized)


def persist_feature_snapshot(snapshot: FeatureSnapshotContract) -> FeatureSnapshotContract:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO risk_feature_snapshots (
                workflow_id, patient_id, conversation_id, model_family, feature_version, feature_snapshot_hash,
                model_input_hash, created_at, symptom_text, structured_features_json, temporal_features_json,
                text_features_hash, label_status, label_source, label_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.workflow_id,
                snapshot.patient_id,
                snapshot.conversation_id,
                snapshot.model_family,
                snapshot.feature_version,
                snapshot.feature_snapshot_hash,
                snapshot.model_input_hash,
                snapshot.created_at,
                snapshot.symptom_text,
                json.dumps(snapshot.structured_features_json, sort_keys=True),
                json.dumps(snapshot.temporal_features_json, sort_keys=True),
                snapshot.text_features_hash,
                snapshot.label_status,
                snapshot.label_source,
                snapshot.label_updated_at,
            ),
        )
        snapshot_id = int(cursor.lastrowid)
    payload = model_dump(snapshot)
    payload["id"] = snapshot_id
    return FeatureSnapshotContract(**payload)


def persist_risk_prediction(prediction: RiskPredictionContract) -> RiskPredictionContract:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO risk_predictions (
                workflow_id, feature_snapshot_id, model_registry_id, created_at, raw_score, calibrated_score, risk_band,
                predicted_specialty, predicted_urgency, predicted_severity, requires_review, threshold_profile_id,
                feature_snapshot_hash, model_input_hash, model_key, model_version, feature_version,
                active_model_key, candidate_model_key, explanations_json, top_features_json, is_shadow_prediction
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prediction.workflow_id,
                prediction.feature_snapshot_id,
                prediction.model_registry_id,
                prediction.created_at,
                prediction.raw_score,
                prediction.calibrated_score,
                prediction.risk_band,
                prediction.predicted_specialty,
                prediction.predicted_urgency,
                prediction.predicted_severity,
                1 if prediction.requires_review else 0,
                prediction.threshold_profile_id,
                prediction.feature_snapshot_hash,
                prediction.model_input_hash,
                prediction.model_key,
                prediction.model_version,
                prediction.feature_version,
                prediction.active_model_key,
                prediction.candidate_model_key,
                json.dumps(prediction.explanations_json, sort_keys=True),
                json.dumps(prediction.top_features_json, sort_keys=True),
                1 if prediction.is_shadow_prediction else 0,
            ),
        )
        prediction_id = int(cursor.lastrowid)
    payload = model_dump(prediction)
    payload["id"] = prediction_id
    return RiskPredictionContract(**payload)


def fetch_active_risk_model() -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM risk_model_registry
            WHERE status = 'active'
            ORDER BY promoted_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise LookupError("No active risk model registry row is available.")
    return row


def fetch_candidate_risk_model() -> sqlite3.Row | None:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT *
            FROM risk_model_registry
            WHERE status = 'candidate'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()


def fetch_active_threshold_profile() -> ThresholdProfileContract:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM risk_threshold_profiles
            WHERE status = 'active'
            ORDER BY promoted_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise LookupError("No active risk threshold profile is available.")
    return ThresholdProfileContract(
        id=int(row["id"]),
        profile_key=str(row["profile_key"]),
        thresholds_json=_parse_json(row["thresholds_json"], {}),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        promoted_at=str(row["promoted_at"]) if row["promoted_at"] else None,
    )


def fetch_candidate_threshold_profile() -> ThresholdProfileContract | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM risk_threshold_profiles
            WHERE status = 'candidate'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    return ThresholdProfileContract(
        id=int(row["id"]),
        profile_key=str(row["profile_key"]),
        thresholds_json=_parse_json(row["thresholds_json"], {}),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        promoted_at=str(row["promoted_at"]) if row["promoted_at"] else None,
    )


def fetch_active_promotion_gate_profile() -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM promotion_gate_profiles
            WHERE status = 'active'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise LookupError("No active promotion gate profile is available.")
    return row


def fetch_feature_snapshot_by_id(feature_snapshot_id: int) -> FeatureSnapshotContract:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM risk_feature_snapshots WHERE id = ?", (feature_snapshot_id,)).fetchone()
    if row is None:
        raise LookupError(f"Feature snapshot {feature_snapshot_id} was not found.")
    return FeatureSnapshotContract(
        id=int(row["id"]),
        workflow_id=str(row["workflow_id"]),
        patient_id=str(row["patient_id"]) if row["patient_id"] else None,
        conversation_id=str(row["conversation_id"]),
        model_family=str(row["model_family"]),
        feature_version=str(row["feature_version"]),
        feature_snapshot_hash=str(row["feature_snapshot_hash"]),
        model_input_hash=str(row["model_input_hash"]),
        created_at=str(row["created_at"]),
        symptom_text=str(row["symptom_text"]),
        structured_features_json=_parse_json(row["structured_features_json"], {}),
        temporal_features_json=_parse_json(row["temporal_features_json"], {}),
        text_features_hash=str(row["text_features_hash"]),
        label_status=str(row["label_status"]),
        label_source=str(row["label_source"] or ""),
        label_updated_at=str(row["label_updated_at"]) if row["label_updated_at"] else None,
    )


def fetch_latest_feature_snapshots(limit: int = 100) -> list[FeatureSnapshotContract]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM risk_feature_snapshots
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [fetch_feature_snapshot_by_id(int(row["id"])) for row in rows]


def fetch_latest_workflow_feature_snapshots(limit: int = 100) -> list[FeatureSnapshotContract]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT rfs.*
            FROM risk_feature_snapshots rfs
            INNER JOIN (
                SELECT workflow_id, MAX(id) AS max_id
                FROM risk_feature_snapshots
                GROUP BY workflow_id
            ) latest ON latest.max_id = rfs.id
            ORDER BY rfs.created_at DESC, rfs.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [fetch_feature_snapshot_by_id(int(row["id"])) for row in rows]


def allocate_live_slot(doctor_name: str, requested_date: str):
    with get_connection() as connection:
        date_floor = max(requested_date, get_current_date().isoformat())
        rows = connection.execute(
            """
            SELECT *
            FROM doctor_slots
            WHERE doctor_name = ? AND slot_date >= ? AND status = 'available'
            ORDER BY CASE WHEN slot_date = ? THEN 0 ELSE 1 END, slot_date ASC, slot_time ASC
            LIMIT 20
            """,
            (doctor_name, date_floor, date_floor),
        ).fetchall()
    return next((row for row in rows if row["slot_time"] and row["slot_date"] and is_future_slot(str(row["slot_date"]), str(row["slot_time"]))), None)


def fetch_available_dates(doctor_name: str, days: int = 7) -> list[dict[str, str]]:
    calendar = build_doctor_calendar(doctor_name, start_date=get_current_date().isoformat(), days=max(days, 1))
    return compact_available_dates(calendar, limit=days)


def _reserve_next_available_slot(connection: sqlite3.Connection, doctor_name: str, requested_date: str, requested_time: str = ""):
    return reserve_best_slot(connection, doctor_name, requested_date, requested_time)


def _mark_reserved_slot_booked(connection: sqlite3.Connection, slot_id: int, appointment_id: int) -> None:
    connection.execute("UPDATE doctor_slots SET status = 'booked', appointment_id = ? WHERE id = ?", (appointment_id, slot_id))


def fetch_doctor_slots(doctor_name: str, days: int = 5) -> list[sqlite3.Row]:
    today = get_current_date().isoformat()
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT id, doctor_name, specialty, branch, slot_date, slot_time, status, appointment_id
            FROM doctor_slots
            WHERE doctor_name = ? AND slot_date >= ?
            ORDER BY slot_date ASC, slot_time ASC
            LIMIT ?
            """,
            (doctor_name, today, days * len(DEFAULT_SLOT_TIMES)),
        ).fetchall()


def update_appointment_status(appointment_id: int, *, queue_state: str | None = None, status: str | None = None, follow_up_status: str | None = None, acknowledged_by: str | None = None) -> None:
    fields = []
    values: list[object] = []
    if queue_state is not None:
        fields.append("queue_state = ?")
        values.append(queue_state)
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if follow_up_status is not None:
        fields.append("follow_up_status = ?")
        values.append(follow_up_status)
    if acknowledged_by is not None:
        fields.append("acknowledged_by = ?")
        values.append(acknowledged_by)
        fields.append("acknowledged_at = ?")
        values.append(dt.datetime.now().isoformat(timespec="seconds"))
    if not fields:
        return
    values.append(appointment_id)
    with get_connection() as connection:
        connection.execute(f"UPDATE appointments SET {', '.join(fields)} WHERE id = ?", values)
    from .appointment_lifecycle import reconcile_lifecycle_from_status

    reconcile_lifecycle_from_status(
        appointment_id,
        queue_state=queue_state,
        status=status,
        follow_up_status=follow_up_status,
        actor_name=acknowledged_by or "system",
        actor_role="doctor" if acknowledged_by else "system",
    )


def update_doctor_notes(appointment_id: int, doctor_notes: str, actor_name: str) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE appointments SET doctor_notes = ?, acknowledged_by = COALESCE(acknowledged_by, ?), acknowledged_at = COALESCE(acknowledged_at, ?) WHERE id = ?",
            (doctor_notes.strip(), actor_name, dt.datetime.now().isoformat(timespec="seconds"), appointment_id),
        )


def get_appointment(appointment_id: int):
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT id, tenant_key, patient_name, patient_email, phone, patient_age, medical_history, symptoms, extracted_symptoms, clinical_questionnaire_json,
                   specialty, doctor_name, branch, appointment_date, slot_time, slot_id, severity, urgency, confidence,
                   priority_score, history_summary, quick_aid, triage_summary, doctor_selection_mode, queue_state, status,
                   created_by, acknowledged_at, acknowledged_by, follow_up_status, reminder_sent,
                   doctor_notes, cancel_reason, created_at
            FROM appointments
            WHERE id = ?
            """,
            (appointment_id,),
        ).fetchone()


def get_patient_history(patient_name: str, phone: str, current_appointment_id: int | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT id, appointment_date, slot_time, specialty, doctor_name, status, follow_up_status, doctor_notes
        FROM appointments
        WHERE patient_name = ? AND phone = ?
    """
    params: list[object] = [patient_name, phone]
    if current_appointment_id is not None:
        query += " AND id <> ?"
        params.append(current_appointment_id)
    query += " ORDER BY appointment_date DESC, slot_time DESC LIMIT 10"
    with get_connection() as connection:
        return connection.execute(query, params).fetchall()


def fetch_latest_clinical_diary(appointment_id: int):
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT id, appointment_id, doctor_name, author_name, diary_text, created_at, updated_at
            FROM clinical_diaries
            WHERE appointment_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (appointment_id,),
        ).fetchone()


def fetch_latest_prescription(appointment_id: int):
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT id, appointment_id, doctor_name, patient_name, author_name, prescription_text, status, delivered_via, created_at, updated_at
            FROM prescriptions
            WHERE appointment_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (appointment_id,),
        ).fetchone()


def save_clinical_diary(appointment_id: int, *, doctor_name: str, author_name: str, diary_text: str) -> int:
    now = dt.datetime.now().isoformat(timespec="seconds")
    existing = fetch_latest_clinical_diary(appointment_id)
    with get_connection() as connection:
        if existing:
            connection.execute(
                """
                UPDATE clinical_diaries
                SET diary_text = ?, author_name = ?, doctor_name = ?, updated_at = ?
                WHERE id = ?
                """,
                (diary_text.strip(), author_name, doctor_name, now, int(existing["id"])),
            )
            return int(existing["id"])
        cursor = connection.execute(
            """
            INSERT INTO clinical_diaries (appointment_id, tenant_key, doctor_name, author_name, diary_text, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (appointment_id, get_current_tenant_key(), doctor_name, author_name, diary_text.strip(), now, now),
        )
        return int(cursor.lastrowid)


def save_prescription_record(appointment_id: int, *, doctor_name: str, patient_name: str, author_name: str, prescription_text: str, delivered_via: str = "whatsapp") -> int:
    now = dt.datetime.now().isoformat(timespec="seconds")
    existing = fetch_latest_prescription(appointment_id)
    with get_connection() as connection:
        if existing:
            connection.execute(
                """
                UPDATE prescriptions
                SET prescription_text = ?, author_name = ?, doctor_name = ?, patient_name = ?, delivered_via = ?, updated_at = ?
                WHERE id = ?
                """,
                (prescription_text.strip(), author_name, doctor_name, patient_name, delivered_via, now, int(existing["id"])),
            )
            return int(existing["id"])
        cursor = connection.execute(
            """
            INSERT INTO prescriptions (
                appointment_id, tenant_key, doctor_name, patient_name, author_name, prescription_text, status, delivered_via, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'issued', ?, ?, ?)
            """,
            (appointment_id, get_current_tenant_key(), doctor_name, patient_name, author_name, prescription_text.strip(), delivered_via, now, now),
        )
        return int(cursor.lastrowid)


def fetch_prescriptions(limit: int = 50) -> list[sqlite3.Row]:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT p.id, p.appointment_id, p.tenant_key, p.doctor_name, p.patient_name, p.author_name, p.prescription_text,
                   p.status, p.delivered_via, p.created_at, p.updated_at, a.appointment_date, a.slot_time
            FROM prescriptions p
            JOIN appointments a ON a.id = p.appointment_id
            ORDER BY p.updated_at DESC, p.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def record_patient_vitals(
    *,
    appointment_id: int | None = None,
    patient_name: str = "",
    patient_email: str = "",
    phone: str = "",
    vitals: dict[str, object] | None = None,
) -> int | None:
    evaluation = evaluate_vitals(vitals)
    normalized = evaluation["vitals"]
    if not any(value not in (None, "") for value in normalized.values()):
        return None
    now = dt.datetime.now().isoformat(timespec="seconds")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO patient_vitals (
                appointment_id, tenant_key, patient_name, patient_email, phone, blood_pressure,
                systolic_bp, diastolic_bp, heart_rate, respiratory_rate, spo2, temperature_f,
                height_cm, weight_kg, risk_level, abnormal_flags_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                appointment_id,
                get_current_tenant_key(),
                patient_name or None,
                patient_email or None,
                phone or None,
                normalized["blood_pressure"] or None,
                normalized["systolic_bp"],
                normalized["diastolic_bp"],
                normalized["heart_rate"],
                normalized["respiratory_rate"],
                normalized["spo2"],
                normalized["temperature_f"],
                normalized["height_cm"],
                normalized["weight_kg"],
                evaluation["level"],
                json.dumps(evaluation["factors"], sort_keys=True),
                now,
            ),
        )
        return int(cursor.lastrowid)


def fetch_latest_patient_vitals(*, appointment_id: int | None = None, phone: str = "", patient_email: str = ""):
    clauses = []
    params: list[object] = []
    if appointment_id is not None:
        clauses.append("appointment_id = ?")
        params.append(appointment_id)
    if phone:
        clauses.append("phone = ?")
        params.append(phone)
    if patient_email:
        clauses.append("lower(patient_email) = ?")
        params.append(patient_email.strip().lower())
    if not clauses:
        return None
    query = "SELECT * FROM patient_vitals WHERE " + " OR ".join(clauses) + " ORDER BY recorded_at DESC, id DESC LIMIT 1"
    with get_connection() as connection:
        return connection.execute(query, params).fetchone()


def create_emergency_escalation(
    *,
    appointment_id: int | None,
    workflow_id: str,
    patient_name: str,
    patient_phone: str,
    patient_email: str,
    risk_level: str,
    risk_score: float,
    summary: str,
    status: str = "active",
) -> int:
    now = dt.datetime.now().isoformat(timespec="seconds")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO emergency_escalations (
                appointment_id, tenant_key, workflow_id, patient_name, patient_phone, patient_email,
                risk_level, risk_score, summary, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                appointment_id,
                get_current_tenant_key(),
                workflow_id,
                patient_name or None,
                patient_phone or None,
                patient_email or None,
                risk_level,
                risk_score,
                summary,
                status,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def fetch_emergency_escalations(appointment_id: int | None = None, workflow_id: str = "", limit: int = 20) -> list[sqlite3.Row]:
    clauses = []
    params: list[object] = []
    if appointment_id is not None:
        clauses.append("appointment_id = ?")
        params.append(appointment_id)
    if workflow_id:
        clauses.append("workflow_id = ?")
        params.append(workflow_id)
    query = "SELECT * FROM emergency_escalations"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as connection:
        return connection.execute(query, params).fetchall()


def fetch_report_analyses(appointment_id: int, limit: int = 10) -> list[sqlite3.Row]:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT * FROM report_analyses
            WHERE appointment_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (appointment_id, limit),
        ).fetchall()


def update_report_review(report_id: int, *, review_status: str, review_notes: str = "") -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE report_analyses
            SET review_status = ?, review_notes = ?
            WHERE id = ?
            """,
            (review_status.strip() or "reviewed", review_notes.strip(), int(report_id)),
        )


def record_report_analysis(
    *,
    appointment_id: int,
    patient_name: str,
    report_type: str,
    file_name: str,
    ocr_status: str,
    lab_values: dict[str, object],
    abnormal_findings: list[dict[str, object]],
) -> int:
    now = dt.datetime.now().isoformat(timespec="seconds")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO report_analyses (
                appointment_id, tenant_key, patient_name, report_type, file_name, ocr_status,
                lab_values_json, abnormal_findings_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                appointment_id,
                get_current_tenant_key(),
                patient_name,
                report_type,
                file_name,
                ocr_status,
                json.dumps(lab_values, sort_keys=True),
                json.dumps(abnormal_findings, sort_keys=True),
                now,
            ),
        )
        report_id = int(cursor.lastrowid)
    log_action(
        "system",
        "report-analysis",
        "analyze-report",
        "appointment",
        appointment_id,
        f"{report_type} report processed with {len(abnormal_findings)} abnormal finding(s)",
    )
    return report_id


def fetch_care_plans(appointment_id: int, limit: int = 5) -> list[sqlite3.Row]:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT * FROM care_plans
            WHERE appointment_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (appointment_id, limit),
        ).fetchall()


def save_care_plan(
    appointment_id: int,
    *,
    doctor_name: str,
    medication_plan: str = "",
    lifestyle_guidance: str = "",
    diet_recommendations: str = "",
    monitoring_tasks: str = "",
    warning_signs: str = "",
    follow_up_schedule: str = "",
    approval_status: str = "approved",
) -> int:
    now = dt.datetime.now().isoformat(timespec="seconds")
    plan = {
        "medication_plan": medication_plan.strip(),
        "medication_schedule": medication_plan.strip(),
        "lifestyle_guidance": lifestyle_guidance.strip(),
        "lifestyle": lifestyle_guidance.strip(),
        "diet_recommendations": diet_recommendations.strip(),
        "monitoring_tasks": monitoring_tasks.strip(),
        "warning_signs": warning_signs.strip(),
        "follow_up_schedule": follow_up_schedule.strip(),
        "follow_up_date": follow_up_schedule.strip(),
    }
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO care_plans (appointment_id, tenant_key, doctor_name, plan_json, approval_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(appointment_id),
                get_current_tenant_key(),
                doctor_name,
                json.dumps(plan, sort_keys=True),
                approval_status.strip() or "approved",
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def fetch_monitoring_checkins(appointment_id: int, limit: int = 10) -> list[sqlite3.Row]:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT * FROM monitoring_checkins
            WHERE appointment_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (appointment_id, limit),
        ).fetchall()


def get_patient_profile(phone: str = "", patient_email: str = ""):
    cleaned_phone = phone.strip()
    cleaned_email = patient_email.strip().lower()
    if not cleaned_phone and not cleaned_email:
        return None
    with get_connection() as connection:
        if cleaned_phone:
            row = connection.execute("SELECT * FROM patient_profiles WHERE phone = ?", (cleaned_phone,)).fetchone()
            if row:
                return row
        if cleaned_email:
            return connection.execute("SELECT * FROM patient_profiles WHERE lower(patient_email) = ?", (cleaned_email,)).fetchone()
    return None


def fetch_patient_appointments(phone: str = "", patient_email: str = "", limit: int = 12) -> list[sqlite3.Row]:
    clauses = []
    params: list[object] = []
    cleaned_phone = phone.strip()
    cleaned_email = patient_email.strip().lower()
    if cleaned_phone:
        clauses.append("phone = ?")
        params.append(cleaned_phone)
    if cleaned_email:
        clauses.append("lower(patient_email) = ?")
        params.append(cleaned_email)
    if not clauses:
        return []
    query = f"""
        SELECT id, patient_name, patient_email, phone, symptoms, specialty, doctor_name, appointment_date, slot_time,
               urgency, status, follow_up_status, queue_state, triage_summary
        FROM appointments
        WHERE {' OR '.join(clauses)}
        ORDER BY appointment_date DESC, slot_time DESC, created_at DESC
        LIMIT ?
    """
    params.append(limit)
    with get_connection() as connection:
        return connection.execute(query, params).fetchall()


def recommend_doctor_for_patient(specialty: str, phone: str = "", patient_email: str = "") -> dict[str, object]:
    normalized_specialty = normalize_specialty(specialty)
    default_info = SPECIALTY_LABELS.get(normalized_specialty, SPECIALTY_LABELS["General"])
    history = fetch_patient_appointments(phone=phone, patient_email=patient_email, limit=20)
    specialty_history = [row for row in history if row["specialty"] == normalized_specialty]
    previous_same_doctor = next((row for row in specialty_history if row["doctor_name"] == default_info["doctor"]), None)
    if previous_same_doctor:
        return {
            "doctor_name": default_info["doctor"],
            "branch": default_info["branch"],
            "continuity_reason": f"Based on your previous {normalized_specialty.lower()} consultations, DOCQ is prioritizing {default_info['doctor']} for continuity of care.",
            "previous_visits_with_doctor": sum(1 for row in specialty_history if row["doctor_name"] == default_info["doctor"]),
        }
    if specialty_history:
        latest = specialty_history[0]
        return {
            "doctor_name": default_info["doctor"],
            "branch": default_info["branch"],
            "continuity_reason": (
                f"DOCQ found prior {normalized_specialty.lower()} care with {latest['doctor_name']} and is keeping this request within the same specialty team."
            ),
            "previous_visits_with_doctor": sum(1 for row in specialty_history if row["doctor_name"] == latest["doctor_name"]),
        }
    return {
        "doctor_name": default_info["doctor"],
        "branch": default_info["branch"],
        "continuity_reason": f"DOCQ is routing this request to the recommended {normalized_specialty.lower()} care team.",
        "previous_visits_with_doctor": 0,
    }


def _doctor_account_by_name(doctor_name: str) -> dict[str, object] | None:
    with get_connection() as connection:
        doctor = connection.execute(
            """
            SELECT name, email, role, branch, doctor_name, specialty, phone
            FROM users
            WHERE doctor_name = ?
              AND role IN ('doctor', 'clinician')
              AND COALESCE(status, 'active') = 'active'
            LIMIT 1
            """,
            (doctor_name,),
        ).fetchone()
    if doctor:
        return {
            "name": str(doctor["name"] or doctor_name),
            "email": str(doctor["email"] or ""),
            "role": str(doctor["role"] or "doctor"),
            "branch": str(doctor["branch"] or "Mysore Central"),
            "doctor_name": str(doctor["doctor_name"] or doctor_name),
            "specialty": str(doctor["specialty"] or "General"),
            "phone": doctor["phone"],
        }
    return next((doctor for doctor in DOCTOR_ACCOUNTS if str(doctor["doctor_name"]) == doctor_name), None)


def _active_doctor_pool() -> list[dict[str, object]]:
    managed_doctors = [
        {
            "doctor_name": str(row["doctor_name"] or row["name"]),
            "specialty": str(row["specialty"] or "General"),
            "branch": str(row["branch"] or "Mysore Central"),
            "name": str(row["name"] or row["doctor_name"]),
            "email": str(row["email"] or ""),
            "role": str(row["role"] or "doctor"),
            "phone": row["phone"],
        }
        for row in fetch_doctor_users(include_inactive=False)
    ]
    if managed_doctors:
        return managed_doctors
    return [dict(doctor) for doctor in DOCTOR_ACCOUNTS]


def recommend_doctor_matches(specialty: str, phone: str = "", patient_email: str = "") -> list[dict[str, object]]:
    normalized_specialty = normalize_specialty(specialty)
    history = fetch_patient_appointments(phone=phone, patient_email=patient_email, limit=20)
    doctor_visit_counts: dict[str, int] = {}
    doctor_last_visit: dict[str, str] = {}
    for row in history:
        if row["specialty"] == normalized_specialty:
            doctor_visit_counts[row["doctor_name"]] = doctor_visit_counts.get(row["doctor_name"], 0) + 1
            if row["doctor_name"] not in doctor_last_visit:
                doctor_last_visit[row["doctor_name"]] = str(row["appointment_date"])

    doctor_pool = [
        {
            "doctor_name": doctor["doctor_name"],
            "specialty": doctor["specialty"],
            "branch": doctor["branch"],
        }
        for doctor in _active_doctor_pool()
        if normalize_specialty(str(doctor["specialty"])) == normalized_specialty
    ]
    if not doctor_pool:
        fallback = SPECIALTY_LABELS.get(normalized_specialty, SPECIALTY_LABELS["General"])
        doctor_pool = [
            {
                "doctor_name": str(fallback["doctor"]),
                "specialty": normalized_specialty,
                "branch": str(fallback["branch"]),
            }
        ]

    most_visited_count = max(doctor_visit_counts.values(), default=0)
    recent_doctor = next((row["doctor_name"] for row in history if row["specialty"] == normalized_specialty), "")
    ranked = []
    for item in doctor_pool:
        doctor_account = _doctor_account_by_name(str(item["doctor_name"]))
        visit_count = doctor_visit_counts.get(item["doctor_name"], 0)
        is_recent = bool(recent_doctor) and item["doctor_name"] == recent_doctor
        is_most_visited = visit_count > 0 and visit_count == most_visited_count
        score = 60 + (visit_count * 25) + (8 if is_recent else 0) + (6 if is_most_visited else 0)
        badges: list[str] = []
        if visit_count > 0:
            badges.append("Recommended")
        if is_recent:
            badges.append("Recently Visited")
        if is_most_visited:
            badges.append("Most Visited")
        available_dates = fetch_available_dates(str(item["doctor_name"]), days=5)
        selection_reason = "Continuity match" if visit_count else "Available doctor in recommended department"
        ranked.append(
            {
                **item,
                "display_name": str(doctor_account["name"]) if doctor_account else str(item["doctor_name"]),
                "department": str(SPECIALTY_LABELS.get(normalized_specialty, SPECIALTY_LABELS["General"])["department"]),
                "score": score,
                "continuity": visit_count > 0,
                "previous_visits": visit_count,
                "recent_visit": is_recent,
                "most_visited": is_most_visited,
                "last_visit_at": doctor_last_visit.get(item["doctor_name"], ""),
                "next_available_slot": (
                    f"{available_dates[0]['date']} {available_dates[0]['first_time']}"
                    if available_dates else "No live slot available"
                ),
                "open_slot_count": sum(int(row["open_count"]) for row in available_dates),
                "selection_reason": selection_reason,
                "badges": badges or ["Available"],
            }
        )
    ranked = rank_doctor_availability(ranked)
    return ranked[:3]


def build_patient_workspace_context(patient_email: str) -> dict[str, object]:
    profile = get_patient_profile(patient_email=patient_email)
    if not profile:
        return {
            "profile": None,
            "recent_visits": [],
            "upcoming_appointments": [],
            "timeline": [],
            "communication_preferences": {},
            "notifications": [],
        }
    appointments = fetch_patient_appointments(phone=str(profile["phone"] or ""), patient_email=patient_email, limit=12)
    upcoming = [row for row in appointments if row["status"] not in {"cancelled"} and row["appointment_date"] >= get_current_date().isoformat()][:4]
    recent = appointments[:6]
    timeline = [
        f"{row['appointment_date']} - {row['specialty']} with {row['doctor_name']}"
        for row in appointments[:5]
    ]
    communication_preferences = _parse_json(profile["communication_preferences_json"], {})
    return {
        "profile": profile,
        "recent_visits": recent,
        "upcoming_appointments": upcoming,
        "timeline": timeline,
        "communication_preferences": communication_preferences,
        "notifications": fetch_notifications(limit=8, target_name=str(profile["patient_name"])),
    }


def upsert_patient_profile(
    patient_name: str,
    phone: str,
    *,
    patient_email: str = "",
    patient_age: int | None = None,
    chronic_conditions: str = "",
    allergies: str = "",
    gender: str = "",
    emergency_contact: str = "",
    communication_preferences: dict[str, Any] | None = None,
    linked_user_id: int | None = None,
    tenant_key: str | None = None,
    last_visit_at: str | None = None,
) -> None:
    now = dt.datetime.now().isoformat(timespec="seconds")
    tenant_key = tenant_key or get_current_tenant_key()
    communication_preferences_json = json.dumps(communication_preferences or {}, sort_keys=True)
    with get_connection() as connection:
        existing = connection.execute("SELECT * FROM patient_profiles WHERE phone = ?", (phone,)).fetchone()
        if existing:
            resolved_email = patient_email.strip() or existing["patient_email"]
            resolved_age = patient_age if patient_age is not None else existing["patient_age"]
            resolved_conditions = chronic_conditions.strip() or str(existing["chronic_conditions"] or "")
            resolved_allergies = allergies.strip() or str(existing["allergies"] or "")
            resolved_gender = gender.strip() or str(existing["gender"] or "")
            resolved_emergency_contact = emergency_contact.strip() or str(existing["emergency_contact"] or "")
            resolved_preferences = communication_preferences_json if communication_preferences is not None else str(existing["communication_preferences_json"] or "{}")
            resolved_tenant_key = tenant_key or str(existing["tenant_key"] or get_current_tenant_key())
            resolved_last_visit = last_visit_at or existing["last_visit_at"]
            resolved_linked_user_id = linked_user_id if linked_user_id is not None else existing["linked_user_id"]
            connection.execute(
                """
                UPDATE patient_profiles
                SET patient_name = ?, patient_email = ?, patient_email_encrypted = ?, phone_encrypted = ?,
                    patient_age = ?, chronic_conditions = ?, chronic_conditions_encrypted = ?, allergies = ?,
                    allergies_encrypted = ?, gender = ?, emergency_contact = ?, emergency_contact_encrypted = ?,
                    communication_preferences_json = ?, linked_user_id = ?, tenant_key = ?, last_visit_at = ?,
                    updated_at = ?
                WHERE phone = ?
                """,
                (
                    patient_name,
                    resolved_email,
                    encrypt_sensitive_value(str(resolved_email).lower()) if resolved_email else None,
                    encrypt_sensitive_value(phone),
                    resolved_age,
                    resolved_conditions or None,
                    encrypt_sensitive_value(resolved_conditions) if resolved_conditions else None,
                    resolved_allergies or None,
                    encrypt_sensitive_value(resolved_allergies) if resolved_allergies else None,
                    resolved_gender or None,
                    resolved_emergency_contact or None,
                    encrypt_sensitive_value(resolved_emergency_contact) if resolved_emergency_contact else None,
                    resolved_preferences,
                    resolved_linked_user_id,
                    resolved_tenant_key,
                    resolved_last_visit,
                    now,
                    phone,
                ),
            )
            return
        connection.execute(
            """
            INSERT INTO patient_profiles (
                patient_name, patient_email, patient_email_encrypted, phone, phone_encrypted, patient_age,
                chronic_conditions, chronic_conditions_encrypted, allergies, allergies_encrypted, gender,
                emergency_contact, emergency_contact_encrypted, communication_preferences_json, linked_user_id, tenant_key,
                last_visit_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patient_name,
                patient_email.strip() or None,
                encrypt_sensitive_value(patient_email.strip().lower()) if patient_email.strip() else None,
                phone,
                encrypt_sensitive_value(phone),
                patient_age,
                chronic_conditions.strip() or None,
                encrypt_sensitive_value(chronic_conditions.strip()) if chronic_conditions.strip() else None,
                allergies.strip() or None,
                encrypt_sensitive_value(allergies.strip()) if allergies.strip() else None,
                gender.strip() or None,
                emergency_contact.strip() or None,
                encrypt_sensitive_value(emergency_contact.strip()) if emergency_contact.strip() else None,
                communication_preferences_json,
                linked_user_id,
                tenant_key,
                last_visit_at,
                now,
                now,
            ),
        )


def link_patient_profile_to_user(*, user_id: int, patient_email: str = "", phone: str = "") -> None:
    if not user_id:
        return
    with get_connection() as connection:
        if phone.strip():
            connection.execute(
                "UPDATE patient_profiles SET linked_user_id = ? WHERE phone = ?",
                (int(user_id), phone.strip()),
            )
        elif patient_email.strip():
            connection.execute(
                "UPDATE patient_profiles SET linked_user_id = ? WHERE lower(patient_email) = ?",
                (int(user_id), patient_email.strip().lower()),
            )


def update_patient_communication_preferences(*, patient_email: str, phone: str = "", communication_preferences: dict[str, Any]) -> None:
    profile = get_patient_profile(phone=phone, patient_email=patient_email)
    if profile is None:
        return
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE patient_profiles
            SET communication_preferences_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(communication_preferences, sort_keys=True),
                dt.datetime.now().isoformat(timespec="seconds"),
                int(profile["id"]),
            ),
        )


def build_history_summary(history_rows: list[sqlite3.Row], symptoms: str) -> str:
    if not history_rows:
        return "No prior clinic visits found for this patient in DOCQ."
    recent_specialties = ", ".join(sorted({row["specialty"] for row in history_rows if row["specialty"]})[:3])
    recurrence = "Recurring symptom pattern flagged." if any((row["doctor_notes"] or "").strip() for row in history_rows[:3]) else "Prior visit history exists."
    return f"{len(history_rows)} prior visit(s) found. Recent specialties: {recent_specialties or 'General'}. {recurrence}"


def create_appointment(payload: dict[str, object], actor_name: str, actor_role: str, config: dict[str, object]) -> dict[str, object]:
    patient_name = str(payload.get("patient_name", "")).strip()
    patient_email = str(payload.get("patient_email", "")).strip()
    phone = str(payload.get("phone", "")).strip()
    raw_age = payload.get("patient_age")
    medical_history = str(payload.get("medical_history", "")).strip()
    symptoms = str(payload.get("symptoms", "")).strip()
    clinical_questionnaire = payload.get("clinical_questionnaire") if isinstance(payload.get("clinical_questionnaire"), dict) else {}
    clinical_questionnaire_summary = format_questionnaire_context(clinical_questionnaire)
    vitals = normalize_vitals(payload.get("vitals") if isinstance(payload.get("vitals"), dict) else {})
    raw_requested_specialty = str(payload.get("specialty", "")).strip()
    requested_specialty = normalize_specialty(raw_requested_specialty) if raw_requested_specialty else ""
    requested_doctor_name = str(payload.get("doctor_name", "")).strip()
    appointment_date = str(payload.get("appointment_date", "")).strip()
    appointment_time = str(payload.get("appointment_time") or payload.get("slot_time") or "").strip()
    patient_age = None
    if raw_age not in (None, ""):
        try:
            patient_age = int(raw_age)
        except (TypeError, ValueError) as exc:
            raise ValueError("Patient age must be a valid number.") from exc

    if not all([patient_name, phone, symptoms, appointment_date]):
        raise ValueError("All appointment fields are required except patient email.")

    scheduled_date = dt.datetime.strptime(appointment_date, "%Y-%m-%d").date()
    if scheduled_date < get_current_date():
        raise ValueError("Appointment date cannot be in the past.")
    created_at = dt.datetime.now().isoformat(timespec="seconds")
    tenant_key = get_current_tenant_key()

    with get_connection() as connection:
        profile = get_patient_profile(phone, patient_email)
        if patient_age is None and profile and profile["patient_age"] is not None:
            patient_age = int(profile["patient_age"])
        if not medical_history and profile and profile["chronic_conditions"]:
            medical_history = str(profile["chronic_conditions"])
        previous_history = connection.execute(
            """
            SELECT id, appointment_date, slot_time, specialty, doctor_name, status, follow_up_status, doctor_notes
            FROM appointments
            WHERE patient_name = ? AND phone = ?
            ORDER BY appointment_date DESC, slot_time DESC
            LIMIT 10
            """,
            (patient_name, phone),
        ).fetchall()
        history_summary = build_history_summary(previous_history, symptoms)
        analysis_symptoms = f"{symptoms}\n{clinical_questionnaire_summary}".strip() if clinical_questionnaire_summary else symptoms
        analysis = analyze_symptoms(analysis_symptoms, patient_age=patient_age, medical_history=medical_history)
        analysis["symptoms"] = symptoms
        analysis["clinical_questionnaire"] = clinical_questionnaire
        analysis["clinical_questionnaire_summary"] = clinical_questionnaire_summary
        analysis["known_context"] = {"used_age": patient_age, "vitals_loaded": any(value not in (None, "") for value in vitals.values())}
        risk_explanation = build_risk_explanation(analysis=analysis, questionnaire_payload=clinical_questionnaire, vitals_payload=vitals)
        analysis["risk_explanation"] = risk_explanation
        analysis["risk_score"] = risk_explanation["risk_score"]
        analysis["clinical_summary"] = build_clinical_summary(analysis)
        if risk_explanation["risk_level"] == "EMERGENCY":
            analysis["urgency"] = "Emergency"
            analysis["severity"] = "Emergency"
            analysis["requires_review"] = True
            analysis["recommended_action"] = "Immediate medical evaluation recommended"
        if previous_history:
            analysis["priority_score"] = round(float(analysis["priority_score"]) + min(len(previous_history) * 2.5, 10.0), 1)
            analysis["triage_summary"] = f"{analysis['triage_summary']} {history_summary}"
        analyzed_specialty = normalize_specialty(str(analysis["specialty"]))
        requested_doctor_account = _doctor_account_by_name(requested_doctor_name)
        requested_doctor_specialty = normalize_specialty(str(requested_doctor_account["specialty"])) if requested_doctor_account else ""
        routing_source = str(analysis.get("department_routing_source", ""))
        if requested_doctor_specialty and requested_specialty and requested_doctor_specialty == requested_specialty:
            specialty = requested_specialty
        elif requested_specialty and routing_source != "department_classification_engine":
            specialty = requested_specialty
        else:
            specialty = analyzed_specialty
        continuity = recommend_doctor_for_patient(specialty, phone=phone, patient_email=patient_email)
        recommended_matches = recommend_doctor_matches(specialty, phone=phone, patient_email=patient_email)
        selected_match = next((item for item in recommended_matches if item["doctor_name"] == requested_doctor_name), None)
        doctor_selection_mode = "patient_selected" if selected_match else "recommended"
        doctor_info = {
            **SPECIALTY_LABELS.get(specialty, SPECIALTY_LABELS["General"]),
            "doctor": str(selected_match["doctor_name"] if selected_match else continuity["doctor_name"]),
            "branch": str(selected_match["branch"] if selected_match else continuity["branch"]),
        }
        queue_state = determine_queue_state(str(analysis["urgency"]), float(analysis["confidence"]))
        status = "scheduled" if queue_state == "awaiting-doctor" else "review"
        connection.execute("BEGIN IMMEDIATE")
        slot = _reserve_next_available_slot(connection, doctor_info["doctor"], appointment_date, appointment_time)
        if not slot:
            connection.rollback()
            raise ValueError("No live slots are available for the selected doctor.")

        cursor = connection.execute(
            """
            INSERT INTO appointments (
                patient_name, patient_email, patient_email_encrypted, phone, phone_encrypted, patient_age, medical_history, medical_history_encrypted, symptoms, extracted_symptoms, clinical_questionnaire_json,
                specialty, doctor_name, tenant_key, branch, appointment_date, slot_time, slot_id, severity, urgency, confidence,
                priority_score, history_summary, quick_aid, triage_summary, doctor_selection_mode, queue_state, status,
                created_by, follow_up_status, reminder_sent, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patient_name,
                patient_email or None,
                encrypt_sensitive_value(patient_email.lower()) if patient_email else None,
                phone,
                encrypt_sensitive_value(phone),
                patient_age,
                medical_history or None,
                encrypt_sensitive_value(medical_history) if medical_history else None,
                symptoms,
                json.dumps(analysis["extracted_symptoms"]),
                json.dumps(clinical_questionnaire, sort_keys=True),
                specialty,
                doctor_info["doctor"],
                tenant_key,
                doctor_info["branch"],
                slot["slot_date"],
                slot["slot_time"],
                slot["id"],
                analysis["severity"],
                analysis["urgency"],
                float(analysis["confidence"]),
                float(analysis["priority_score"]),
                history_summary,
                "\n".join(analysis["quick_aid"]),
                analysis["triage_summary"],
                doctor_selection_mode,
                queue_state,
                status,
                actor_name,
                "scheduled",
                0,
                created_at,
            ),
        )
        appointment_id = cursor.lastrowid
        _mark_reserved_slot_booked(connection, slot["id"], appointment_id)
        connection.commit()
    record_patient_vitals(
        appointment_id=int(appointment_id),
        patient_name=patient_name,
        patient_email=patient_email,
        phone=phone,
        vitals=vitals,
    )
    upsert_patient_profile(
        patient_name,
        phone,
        patient_email=patient_email,
        patient_age=patient_age,
        chronic_conditions=medical_history,
        tenant_key=tenant_key,
        last_visit_at=slot["slot_date"],
    )

    appointment = {
        "id": appointment_id,
        "patient_name": patient_name,
        "patient_email": patient_email,
        "phone": phone,
        "patient_age": patient_age,
        "medical_history": medical_history,
        "symptoms": symptoms,
        "severity": analysis["severity"],
        "specialty": specialty,
        "department": SPECIALTY_LABELS.get(specialty, SPECIALTY_LABELS["General"])["department"],
        "department_category": analysis.get("department_category", "General Symptoms"),
        "department_confidence": analysis.get("department_confidence", 0),
        "department_routing_reason": analysis.get("department_routing_reason", ""),
        "department_routing_source": analysis.get("department_routing_source", ""),
        "doctor_name": doctor_info["doctor"],
        "tenant_key": tenant_key,
        "branch": doctor_info["branch"],
        "appointment_date": slot["slot_date"],
        "slot_time": slot["slot_time"],
        "urgency": analysis["urgency"],
        "confidence": analysis["confidence"],
        "priority_score": analysis["priority_score"],
        "history_summary": history_summary,
        "quick_aid": analysis["quick_aid"],
        "triage_summary": analysis["triage_summary"],
        "clinical_questionnaire": clinical_questionnaire,
        "clinical_questionnaire_summary": clinical_questionnaire_summary,
        "risk_score": analysis["risk_score"],
        "risk_explanation": analysis["risk_explanation"],
        "clinical_summary": analysis["clinical_summary"],
        "vitals": vitals,
        "continuity_reason": continuity["continuity_reason"],
        "doctor_matches": recommended_matches,
        "doctor_selection_mode": doctor_selection_mode,
        "queue_state": queue_state,
        "status": status,
    }
    print(
        "[BOOKING FLOW] before notify_automation",
        {
            "appointment_id": int(appointment_id),
            "doctor_name": doctor_info["doctor"],
            "patient_name": patient_name,
            "queue_state": queue_state,
            "status": status,
        },
        flush=True,
    )
    notify_automation(config, appointment)
    print(
        "[BOOKING FLOW] after notify_automation",
        {
            "appointment_id": int(appointment_id),
        },
        flush=True,
    )
    from .appointment_lifecycle import initialize_appointment_lifecycle

    initialize_appointment_lifecycle(appointment, actor_name=actor_name, actor_role=actor_role)
    log_action(actor_name, actor_role, "create-appointment", "appointment", appointment_id, f"{patient_name} -> {doctor_info['doctor']} {slot['slot_date']} {slot['slot_time']}")
    return appointment


def reschedule_appointment(appointment_id: int, new_date: str, actor_name: str, actor_role: str) -> None:
    appointment = get_appointment(appointment_id)
    if not appointment:
        raise ValueError("Appointment not found.")
    requested_date = dt.datetime.strptime(new_date, "%Y-%m-%d").date()
    if requested_date < get_current_date():
        raise ValueError("New appointment date cannot be in the past.")
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        new_slot = _reserve_next_available_slot(connection, appointment["doctor_name"], new_date)
        if not new_slot:
            connection.rollback()
            raise ValueError("No replacement slot is available for this doctor.")
        connection.execute("UPDATE doctor_slots SET status = 'available', appointment_id = NULL WHERE id = ?", (appointment["slot_id"],))
        connection.execute(
            """
            UPDATE appointments
            SET appointment_date = ?, slot_time = ?, slot_id = ?, queue_state = 'awaiting-doctor',
                status = 'rescheduled', reminder_sent = 0
            WHERE id = ?
            """,
            (new_slot["slot_date"], new_slot["slot_time"], new_slot["id"], appointment_id),
        )
        _mark_reserved_slot_booked(connection, new_slot["id"], appointment_id)
        connection.commit()
    from .appointment_lifecycle import transition_appointment_lifecycle

    transition_appointment_lifecycle(
        appointment_id,
        to_state="reassignment_pending",
        cause=f"appointment rescheduled to {new_slot['slot_date']} {new_slot['slot_time']}",
        actor_name=actor_name,
        actor_role=actor_role,
    )
    _notify_appointment_governance_change(
        appointment,
        action="Appointment Updated",
        actor_name=actor_name,
        reason=f"Administrative rescheduling by {actor_name}",
        previous_slot=f"{appointment['appointment_date']} {appointment['slot_time']}",
        new_slot=f"{new_slot['slot_date']} {new_slot['slot_time']}",
        doctor_name=str(appointment["doctor_name"]),
    )
    log_action(actor_name, actor_role, "reschedule-appointment", "appointment", appointment_id, f"rescheduled to {new_slot['slot_date']} {new_slot['slot_time']}")


def _notify_appointment_governance_change(
    appointment,
    *,
    action: str,
    actor_name: str,
    reason: str,
    previous_slot: str,
    new_slot: str,
    doctor_name: str,
) -> None:
    patient_message = (
        f"{action}\n\nYour appointment has been updated.\n\n"
        f"Doctor: {doctor_name}\nPrevious Slot: {previous_slot}\nNew Slot: {new_slot}\nReason: {reason}"
    )
    doctor_message = (
        f"Schedule Updated\n\nPatient: {appointment['patient_name']}\n"
        f"Previous Slot: {previous_slot}\nNew Slot: {new_slot}\nReason: {reason}"
    )
    admin_message = (
        f"Governance action recorded by {actor_name}: {action}. "
        f"Patient {appointment['patient_name']} moved from {previous_slot} to {new_slot}. Reason: {reason}"
    )
    create_notification(
        int(appointment["id"]),
        "patient",
        str(appointment["patient_name"]),
        "dashboard",
        patient_message,
        status="visible",
        tenant_key=str(appointment["tenant_key"]),
        correlation_id=f"appointment:{appointment['id']}:governance:patient",
        message_category="appointment_governance",
    )
    create_notification(
        int(appointment["id"]),
        "doctor",
        doctor_name,
        "dashboard",
        doctor_message,
        status="visible",
        tenant_key=str(appointment["tenant_key"]),
        correlation_id=f"appointment:{appointment['id']}:governance:doctor",
        message_category="appointment_governance",
    )
    create_notification(
        int(appointment["id"]),
        "admin",
        actor_name,
        "dashboard",
        admin_message,
        status="visible",
        tenant_key=str(appointment["tenant_key"]),
        correlation_id=f"appointment:{appointment['id']}:governance:admin",
        message_category="appointment_governance",
    )


def reassign_appointment_doctor(appointment_id: int, doctor_name: str, reason: str, actor_name: str, actor_role: str) -> None:
    appointment = get_appointment(appointment_id)
    if not appointment:
        raise ValueError("Appointment not found.")
    doctor = _doctor_account_by_name(doctor_name)
    if doctor is None:
        raise ValueError("Selected doctor is not available in DOCQ.")
    requested_date = str(appointment["appointment_date"] or get_current_date().isoformat())
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        new_slot = _reserve_next_available_slot(connection, doctor_name, requested_date)
        if not new_slot:
            connection.rollback()
            raise ValueError("No replacement slot is available for the selected doctor.")
        if appointment["slot_id"]:
            connection.execute("UPDATE doctor_slots SET status = 'available', appointment_id = NULL WHERE id = ?", (appointment["slot_id"],))
        connection.execute(
            """
            UPDATE appointments
            SET specialty = ?, doctor_name = ?, branch = ?, appointment_date = ?, slot_time = ?, slot_id = ?,
                doctor_selection_mode = 'admin_override', queue_state = 'awaiting-doctor',
                status = 'rescheduled', reminder_sent = 0
            WHERE id = ?
            """,
            (
                doctor["specialty"],
                doctor["doctor_name"],
                doctor["branch"],
                new_slot["slot_date"],
                new_slot["slot_time"],
                new_slot["id"],
                appointment_id,
            ),
        )
        _mark_reserved_slot_booked(connection, new_slot["id"], appointment_id)
        connection.commit()
    from .appointment_lifecycle import transition_appointment_lifecycle

    transition_appointment_lifecycle(
        appointment_id,
        to_state="reassignment_pending",
        cause=f"doctor reassigned to {doctor['doctor_name']} by {actor_name}",
        actor_name=actor_name,
        actor_role=actor_role,
    )
    _notify_appointment_governance_change(
        appointment,
        action="Doctor Reassigned",
        actor_name=actor_name,
        reason=reason.strip() or "Administrative doctor reassignment",
        previous_slot=f"{appointment['doctor_name']} / {appointment['appointment_date']} {appointment['slot_time']}",
        new_slot=f"{doctor['doctor_name']} / {new_slot['slot_date']} {new_slot['slot_time']}",
        doctor_name=str(doctor["doctor_name"]),
    )
    log_action(
        actor_name,
        actor_role,
        "reassign-doctor",
        "appointment",
        appointment_id,
        f"{appointment['doctor_name']} -> {doctor['doctor_name']} ({reason.strip() or 'Administrative doctor reassignment'})",
    )


def escalate_appointment_priority(appointment_id: int, reason: str, actor_name: str, actor_role: str) -> None:
    appointment = get_appointment(appointment_id)
    if not appointment:
        raise ValueError("Appointment not found.")
    normalized_reason = reason.strip() or "Administrative priority escalation"
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE appointments
            SET queue_state = 'priority-review', status = 'urgent-review',
                urgency = CASE WHEN urgency = 'Emergency' THEN urgency ELSE 'High' END,
                priority_score = CASE WHEN priority_score IS NULL OR priority_score < 85 THEN 85 ELSE priority_score END
            WHERE id = ?
            """,
            (appointment_id,),
        )
    from .appointment_lifecycle import transition_appointment_lifecycle

    transition_appointment_lifecycle(
        appointment_id,
        to_state="doctor_review",
        cause=f"priority escalated by {actor_name}: {normalized_reason}",
        actor_name=actor_name,
        actor_role=actor_role,
    )
    create_notification(
        appointment_id,
        "doctor",
        str(appointment["doctor_name"]),
        "dashboard",
        f"Priority Escalated\n\nPatient: {appointment['patient_name']}\nReason: {normalized_reason}",
        status="visible",
        tenant_key=str(appointment["tenant_key"]),
        correlation_id=f"appointment:{appointment_id}:governance:priority",
        message_category="appointment_governance",
    )
    create_notification(
        appointment_id,
        "admin",
        actor_name,
        "dashboard",
        f"Priority escalation recorded for {appointment['patient_name']}. Reason: {normalized_reason}",
        status="visible",
        tenant_key=str(appointment["tenant_key"]),
        correlation_id=f"appointment:{appointment_id}:governance:priority-admin",
        message_category="appointment_governance",
    )
    log_action(actor_name, actor_role, "escalate-priority", "appointment", appointment_id, normalized_reason)


def cancel_appointment(appointment_id: int, reason: str, actor_name: str, actor_role: str) -> None:
    appointment = get_appointment(appointment_id)
    if not appointment:
        raise ValueError("Appointment not found.")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE appointments
            SET status = 'cancelled', queue_state = 'cancelled', cancel_reason = ?, follow_up_status = 'none'
            WHERE id = ?
            """,
            (reason.strip() or "Cancelled by clinic", appointment_id),
        )
        connection.execute("UPDATE doctor_slots SET status = 'available', appointment_id = NULL WHERE id = ?", (appointment["slot_id"],))
    from .appointment_lifecycle import transition_appointment_lifecycle

    transition_appointment_lifecycle(
        appointment_id,
        to_state="workflow_closed",
        cause=reason.strip() or "Cancelled by clinic",
        actor_name=actor_name,
        actor_role=actor_role,
    )
    log_action(actor_name, actor_role, "cancel-appointment", "appointment", appointment_id, reason.strip() or "Cancelled by clinic")


def fetch_appointments(limit: int | None = None, doctor_name: str | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT id, tenant_key, patient_name, patient_email, phone, patient_age, medical_history, symptoms, extracted_symptoms, clinical_questionnaire_json,
               specialty, doctor_name, branch, appointment_date, slot_time, slot_id, severity, urgency, confidence,
               priority_score, history_summary, quick_aid, triage_summary, doctor_selection_mode, queue_state, status,
               created_by, acknowledged_at, acknowledged_by, follow_up_status, reminder_sent,
               doctor_notes, cancel_reason, created_at
        FROM appointments
    """
    clauses = []
    params: list[object] = []
    if doctor_name:
        clauses.append("doctor_name = ?")
        params.append(doctor_name)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY appointment_date ASC, slot_time ASC, created_at DESC"
    if limit is not None:
        query += f" LIMIT {int(limit)}"
    with get_connection() as connection:
        return connection.execute(query, params).fetchall()


def fetch_notifications(limit: int = 16, target_name: str | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT id, appointment_id, tenant_key, target_type, target_name, channel, message, status, external_id, correlation_id,
               attempt_count, next_attempt_at, last_error, acknowledged_at, provider_metadata_json, message_category, created_at
        FROM notifications
    """
    params: list[object] = []
    if target_name:
        query += " WHERE target_name = ?"
        params.append(target_name)
    query += " ORDER BY created_at DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    with get_connection() as connection:
        return connection.execute(query, params).fetchall()


def fetch_notifications_for_appointment(appointment_id: int, limit: int = 20) -> list[sqlite3.Row]:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT id, appointment_id, tenant_key, target_type, target_name, channel, message, status, external_id, correlation_id,
                   attempt_count, next_attempt_at, last_error, acknowledged_at, provider_metadata_json, message_category, created_at
            FROM notifications
            WHERE appointment_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (int(appointment_id), int(limit)),
        ).fetchall()


def fetch_appointment_lifecycle_transitions(appointment_id: int, limit: int = 100) -> list[sqlite3.Row]:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT *
            FROM appointment_lifecycle_transitions
            WHERE appointment_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (appointment_id, limit),
        ).fetchall()


def fetch_sla_violations(limit: int = 100) -> list[sqlite3.Row]:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT *
            FROM sla_violations
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def fetch_coordination_queue_items(queue_type: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
    query = "SELECT * FROM coordination_queue_items"
    params: list[object] = []
    if queue_type:
        query += " WHERE queue_type = ?"
        params.append(queue_type)
    query += " ORDER BY priority DESC, created_at ASC LIMIT ?"
    params.append(limit)
    with get_connection() as connection:
        return connection.execute(query, params).fetchall()


def fetch_calendar_sync_runs(limit: int = 100) -> list[sqlite3.Row]:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT *
            FROM calendar_sync_runs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def fetch_audit_logs(limit: int = 12) -> list[sqlite3.Row]:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT actor_name, actor_role, action, entity_type, entity_id, details, created_at
            FROM audit_logs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def fetch_automation_runs(limit: int = 12) -> list[sqlite3.Row]:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT workflow_name, status, details, processed_count, created_at
            FROM automation_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def fetch_workflow_events(workflow_id: str, limit: int = 40) -> list[sqlite3.Row]:
    return replay_repository.fetch_workflow_events(workflow_id, limit=limit)


def fetch_recent_workflow_events(limit: int = 25) -> list[sqlite3.Row]:
    rows = replay_repository.fetch_recent_workflow_events(limit=max(limit * 3, 25))
    filtered = [
        row
        for row in rows
        if not str(row["workflow_id"]).startswith(EVALUATION_WORKFLOW_PREFIX)
        and not str(row["workflow_id"]).startswith(GOVERNANCE_WORKFLOW_PREFIX)
        and not str(row["workflow_id"]).startswith(SECURITY_WORKFLOW_PREFIX)
    ]
    return filtered[:limit]


def build_workflow_replay(workflow_id: str, limit: int = 40) -> dict[str, object]:
    hydration = hydrate_workflow_replay(workflow_id, limit=max(limit, 200))
    replay = hydration.replay
    if replay.step_count > limit:
        replay = WorkflowReplay(
            workflow_id=replay.workflow_id,
            step_count=min(limit, len(replay.steps[-limit:])),
            latest_decision=replay.latest_decision,
            steps=replay.steps[-limit:],
        )
    return model_dump(replay)


def record_evaluation_event(
    evaluation_run_key: str,
    *,
    action: str,
    decision: str,
    payload: dict[str, Any],
    confidence: float | None = None,
    reasons: list[str] | None = None,
) -> int:
    return record_workflow_event(
        f"{EVALUATION_WORKFLOW_PREFIX}{evaluation_run_key}",
        trace_id=f"{EVALUATION_WORKFLOW_PREFIX}{evaluation_run_key}",
        correlation_id=evaluation_run_key,
        stage="evaluation",
        agent="model-evaluation",
        action=action,
        decision=decision,
        confidence=confidence,
        reasons=reasons or [],
        payload=payload,
    )


def record_governance_event(
    governance_key: str,
    *,
    action: str,
    decision: str,
    payload: dict[str, Any],
    confidence: float | None = None,
    reasons: list[str] | None = None,
) -> int:
    return record_workflow_event(
        f"{GOVERNANCE_WORKFLOW_PREFIX}{governance_key}",
        trace_id=f"{GOVERNANCE_WORKFLOW_PREFIX}{governance_key}",
        correlation_id=governance_key,
        stage="governance",
        agent="governance-runtime",
        action=action,
        decision=decision,
        confidence=confidence,
        reasons=reasons or [],
        payload=payload,
    )


def record_security_event(
    security_key: str,
    *,
    action: str,
    decision: str,
    payload: dict[str, Any],
    confidence: float | None = None,
    reasons: list[str] | None = None,
) -> int:
    return record_workflow_event(
        f"{SECURITY_WORKFLOW_PREFIX}{security_key}",
        trace_id=f"{SECURITY_WORKFLOW_PREFIX}{security_key}",
        correlation_id=security_key,
        stage="security",
        agent="security-runtime",
        action=action,
        decision=decision,
        confidence=confidence,
        reasons=reasons or [],
        payload=payload,
    )


def build_workflow_model_diff(workflow_id: str) -> dict[str, object] | None:
    rows = fetch_workflow_predictions(workflow_id)
    active_row = next((row for row in reversed(rows) if not bool(row["is_shadow_prediction"])), None)
    candidate_row = next((row for row in reversed(rows) if bool(row["is_shadow_prediction"])), None)
    if active_row is None or candidate_row is None:
        return None
    active_prediction = build_risk_prediction_contract(active_row)
    candidate_prediction = build_risk_prediction_contract(candidate_row)
    comparison = ShadowPredictionComparison(**model_dump(build_shadow_comparison(workflow_id, active_prediction, candidate_prediction)))
    return model_dump(comparison)


def fetch_latest_workflow_snapshots(limit: int = 100) -> list[sqlite3.Row]:
    return replay_repository.fetch_latest_workflow_snapshots(
        evaluation_prefix=EVALUATION_WORKFLOW_PREFIX,
        governance_prefix=GOVERNANCE_WORKFLOW_PREFIX,
        security_prefix=SECURITY_WORKFLOW_PREFIX,
        limit=limit,
    )


def fetch_workflow_predictions(workflow_id: str) -> list[sqlite3.Row]:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT *
            FROM risk_predictions
            WHERE workflow_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (workflow_id,),
        ).fetchall()


def persist_model_evaluation_run(run: ModelEvaluationRun) -> ModelEvaluationRun:
    return evaluation_repository.persist_model_evaluation_run(run)


def update_model_evaluation_run(run_id: int, *, status: str, workflow_count: int, replay_integrity_passed: bool, evaluation_checksum: str, summary_json: EvaluationSummary, promotion_recommendation: str, promotion_gate_result: PromotionGateResult | None, completed_at: str | None) -> None:
    evaluation_repository.update_model_evaluation_run(
        run_id,
        status=status,
        workflow_count=workflow_count,
        replay_integrity_passed=replay_integrity_passed,
        evaluation_checksum=evaluation_checksum,
        summary_json=summary_json,
        promotion_recommendation=promotion_recommendation,
        promotion_gate_result=promotion_gate_result,
        completed_at=completed_at,
    )


def persist_model_evaluation_result(result: ModelEvaluationResult) -> ModelEvaluationResult:
    return evaluation_repository.persist_model_evaluation_result(result)


def persist_evaluation_drift_snapshot(snapshot: EvaluationDriftSnapshot) -> EvaluationDriftSnapshot:
    return evaluation_repository.persist_evaluation_drift_snapshot(snapshot)


def fetch_model_evaluation_runs(limit: int = 20) -> list[sqlite3.Row]:
    return evaluation_repository.fetch_model_evaluation_runs(limit=limit)


def fetch_model_evaluation_run(run_id: int) -> sqlite3.Row | None:
    return evaluation_repository.fetch_model_evaluation_run(run_id)


def fetch_model_evaluation_results(run_id: int) -> list[sqlite3.Row]:
    return evaluation_repository.fetch_model_evaluation_results(run_id)


def fetch_evaluation_drift_snapshot(run_id: int) -> sqlite3.Row | None:
    return evaluation_repository.fetch_evaluation_drift_snapshot(run_id)


def build_model_evaluation_run_contract(row: sqlite3.Row) -> ModelEvaluationRun:
    return ModelEvaluationRun(
        id=int(row["id"]),
        evaluation_run_key=str(row["evaluation_run_key"]),
        candidate_model_registry_id=int(row["candidate_model_registry_id"]),
        active_model_registry_id=int(row["active_model_registry_id"]),
        candidate_threshold_profile_id=int(row["candidate_threshold_profile_id"]),
        active_threshold_profile_id=int(row["active_threshold_profile_id"]),
        evaluation_scope=str(row["evaluation_scope"]),
        workflow_count=int(row["workflow_count"]),
        started_at=str(row["started_at"]),
        completed_at=str(row["completed_at"]) if row["completed_at"] else None,
        status=str(row["status"]),
        replay_integrity_passed=bool(row["replay_integrity_passed"]),
        evaluation_checksum=str(row["evaluation_checksum"] or ""),
        summary_json=EvaluationSummary(**_parse_json(row["summary_json"], {"evaluation_checksum": ""})),
        promotion_recommendation=str(row["promotion_recommendation"] or "hold"),
        promotion_gate_result=PromotionGateResult(**_parse_json(row["promotion_gate_result"], {})) if row["promotion_gate_result"] else None,
    )


def build_model_evaluation_result_contract(row: sqlite3.Row) -> ModelEvaluationResult:
    return ModelEvaluationResult(
        id=int(row["id"]),
        evaluation_run_id=int(row["evaluation_run_id"]),
        workflow_id=str(row["workflow_id"]),
        feature_snapshot_id=int(row["feature_snapshot_id"]),
        replay_integrity_status=str(row["replay_integrity_status"]),
        active_prediction_id=int(row["active_prediction_id"]),
        candidate_prediction_id=int(row["candidate_prediction_id"]),
        active_policy_path=str(row["active_policy_path"]),
        candidate_policy_path=str(row["candidate_policy_path"]),
        escalation_delta=bool(row["escalation_delta"]),
        review_delta=bool(row["review_delta"]),
        threshold_delta=str(row["threshold_delta"] or ""),
        severity_delta=str(row["severity_delta"] or ""),
        specialty_delta=str(row["specialty_delta"] or ""),
        calibration_delta=float(row["calibration_delta"]),
        false_negative_risk=bool(row["false_negative_risk"]),
        divergence_summary_json=_parse_json(row["divergence_summary_json"], {}),
    )


def build_evaluation_drift_snapshot_contract(row: sqlite3.Row) -> EvaluationDriftSnapshot:
    return EvaluationDriftSnapshot(
        id=int(row["id"]),
        evaluation_run_id=int(row["evaluation_run_id"]),
        score_distribution_delta=float(row["score_distribution_delta"]),
        specialty_distribution_delta=float(row["specialty_distribution_delta"]),
        review_rate_delta=float(row["review_rate_delta"]),
        escalation_delta=float(row["escalation_delta"]),
        false_negative_delta=float(row["false_negative_delta"]),
        calibration_error_delta=float(row["calibration_error_delta"]),
        created_at=str(row["created_at"]),
    )


def persist_governance_recommendation(recommendation: GovernanceRecommendation) -> GovernanceRecommendation:
    return governance_repository.persist_governance_recommendation(recommendation)


def persist_governance_timeline_event(event: GovernanceTimelineEvent) -> GovernanceTimelineEvent:
    return governance_repository.persist_governance_timeline_event(event)


def fetch_governance_recommendations(limit: int = 50) -> list[sqlite3.Row]:
    return governance_repository.fetch_governance_recommendations(limit=limit)


def fetch_governance_recommendation(recommendation_id: int) -> sqlite3.Row | None:
    return governance_repository.fetch_governance_recommendation(recommendation_id)


def fetch_rollout_profiles(limit: int = 20) -> list[sqlite3.Row]:
    return governance_repository.fetch_rollout_profiles(limit=limit)


def fetch_active_rollout_profile() -> sqlite3.Row | None:
    return governance_repository.fetch_active_rollout_profile()


def fetch_governance_timeline(limit: int = 100) -> list[sqlite3.Row]:
    return governance_repository.fetch_governance_timeline(limit=limit)


def fetch_drift_trigger_rules(limit: int = 50) -> list[sqlite3.Row]:
    return governance_repository.fetch_drift_trigger_rules(limit=limit)


def build_governance_recommendation_contract(row: sqlite3.Row) -> GovernanceRecommendation:
    return GovernanceRecommendation(
        id=int(row["id"]),
        recommendation_key=str(row["recommendation_key"]),
        recommendation_type=str(row["recommendation_type"]),
        source_evaluation_run_id=int(row["source_evaluation_run_id"]) if row["source_evaluation_run_id"] is not None else None,
        candidate_model_registry_id=int(row["candidate_model_registry_id"]) if row["candidate_model_registry_id"] is not None else None,
        threshold_profile_id=int(row["threshold_profile_id"]) if row["threshold_profile_id"] is not None else None,
        recommendation_status=str(row["recommendation_status"]),
        recommendation_reason=str(row["recommendation_reason"]),
        confidence_score=float(row["confidence_score"]),
        created_at=str(row["created_at"]),
        resolved_at=str(row["resolved_at"]) if row["resolved_at"] else None,
        supporting_evidence_json=_parse_json(row["supporting_evidence_json"], {}),
    )


def build_rollout_profile_contract(row: sqlite3.Row) -> RolloutSimulationProfile:
    return RolloutSimulationProfile(
        id=int(row["id"]),
        rollout_profile_key=str(row["rollout_profile_key"]),
        rollout_percentages_json=_parse_json(row["rollout_percentages_json"], []),
        safety_constraints_json=_parse_json(row["safety_constraints_json"], {}),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
    )


def build_governance_timeline_event_contract(row: sqlite3.Row) -> GovernanceTimelineEvent:
    return GovernanceTimelineEvent(
        id=int(row["id"]),
        governance_entity_type=str(row["governance_entity_type"]),
        governance_entity_id=int(row["governance_entity_id"]),
        event_type=str(row["event_type"]),
        event_timestamp=str(row["event_timestamp"]),
        related_model_key=str(row["related_model_key"] or ""),
        related_threshold_profile_key=str(row["related_threshold_profile_key"] or ""),
        incident_correlation_id=str(row["incident_correlation_id"] or ""),
        payload_json=_parse_json(row["payload_json"], {}),
    )


def build_drift_trigger_contract(row: sqlite3.Row, *, triggered: bool = False, source: str = "", governance_checksum: str = "") -> DriftTriggerResult:
    return DriftTriggerResult(
        rule_key=str(row["rule_key"]),
        drift_metric_type=str(row["drift_metric_type"]),
        threshold_value=float(row["threshold_value"]),
        trigger_action=str(row["trigger_action"]),
        cooldown_minutes=int(row["cooldown_minutes"]),
        triggered=triggered,
        drift_trigger_source=source,
        governance_checksum=governance_checksum,
    )


def build_governance_state_snapshot(
    *,
    governance_checksum: str,
    recommendation_confidence: float,
    rollout_risk_score: float,
    rollback_risk_score: float,
    incident_correlation: dict[str, Any] | None,
    active_recommendations: list[GovernanceRecommendation],
    drift_triggers: list[DriftTriggerResult],
    rollout_profiles: list[RolloutSimulationProfile],
    timeline: list[GovernanceTimelineEvent],
) -> GovernanceStateSnapshot:
    return GovernanceStateSnapshot(
        governance_checksum=governance_checksum,
        recommendation_confidence=recommendation_confidence,
        rollout_risk_score=rollout_risk_score,
        rollback_risk_score=rollback_risk_score,
        incident_correlation=incident_correlation,
        active_recommendations=active_recommendations,
        drift_triggers=drift_triggers,
        rollout_profiles=rollout_profiles,
        timeline=timeline,
    )


def build_risk_prediction_contract(row: sqlite3.Row) -> RiskPredictionContract:
    return RiskPredictionContract(
        id=int(row["id"]),
        workflow_id=str(row["workflow_id"]),
        feature_snapshot_id=int(row["feature_snapshot_id"]),
        model_registry_id=int(row["model_registry_id"]),
        created_at=str(row["created_at"]),
        raw_score=float(row["raw_score"]),
        calibrated_score=float(row["calibrated_score"]),
        risk_band=str(row["risk_band"]),
        predicted_specialty=str(row["predicted_specialty"]),
        predicted_urgency=str(row["predicted_urgency"]),
        predicted_severity=str(row["predicted_severity"]),
        requires_review=bool(row["requires_review"]),
        threshold_profile_id=int(row["threshold_profile_id"]),
        feature_snapshot_hash=str(row["feature_snapshot_hash"]),
        model_input_hash=str(row["model_input_hash"]),
        model_key=str(row["model_key"]),
        model_version=str(row["model_version"]),
        feature_version=str(row["feature_version"]),
        active_model_key=str(row["active_model_key"] or ""),
        candidate_model_key=str(row["candidate_model_key"] or ""),
        explanations_json=_parse_json(row["explanations_json"], {}),
        top_features_json=_parse_json(row["top_features_json"], []),
        is_shadow_prediction=bool(row["is_shadow_prediction"]),
    )


def fetch_workflow_lifecycle_stats(limit: int = 200) -> list[sqlite3.Row]:
    return replay_repository.fetch_workflow_lifecycle_stats(
        evaluation_prefix=EVALUATION_WORKFLOW_PREFIX,
        governance_prefix=GOVERNANCE_WORKFLOW_PREFIX,
        security_prefix=SECURITY_WORKFLOW_PREFIX,
        limit=limit,
    )


def fetch_recent_prediction_pairs(limit: int = 200) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT ap.workflow_id,
                   ap.raw_score AS active_raw_score,
                   ap.calibrated_score AS active_calibrated_score,
                   ap.risk_band AS active_risk_band,
                   ap.predicted_specialty AS active_predicted_specialty,
                   ap.requires_review AS active_requires_review,
                   cp.raw_score AS candidate_raw_score,
                   cp.calibrated_score AS candidate_calibrated_score,
                   cp.risk_band AS candidate_risk_band,
                   cp.predicted_specialty AS candidate_predicted_specialty,
                   cp.requires_review AS candidate_requires_review,
                   COALESCE(we.decision, 'pending') AS policy_decision
            FROM risk_predictions ap
            INNER JOIN risk_predictions cp
                ON cp.workflow_id = ap.workflow_id
               AND cp.is_shadow_prediction = 1
            LEFT JOIN (
                SELECT workflow_id, MAX(id) AS max_id
                FROM workflow_events
                GROUP BY workflow_id
            ) latest ON latest.workflow_id = ap.workflow_id
            LEFT JOIN workflow_events we ON we.id = latest.max_id
            WHERE ap.is_shadow_prediction = 0
            ORDER BY ap.created_at DESC, ap.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_tool_execution_logs(limit: int = 200) -> list[sqlite3.Row]:
    return telemetry_repository.fetch_tool_execution_logs(limit=limit)


def build_drift_detection_summary() -> dict[str, object]:
    active_model = fetch_active_risk_model()
    candidate_model = fetch_candidate_risk_model()
    summary = build_drift_summary(
        active_model_key=str(active_model["model_key"]),
        candidate_model_key=str(candidate_model["model_key"]) if candidate_model else "",
        rows=fetch_recent_prediction_pairs(limit=200),
    )
    return model_dump(summary)


def build_model_governance_summary(workflow_id: str = "") -> dict[str, object]:
    active_model = fetch_active_risk_model()
    candidate_model = fetch_candidate_risk_model()
    active_threshold = fetch_active_threshold_profile()
    candidate_threshold = fetch_candidate_threshold_profile()
    rows = fetch_recent_prediction_pairs(limit=200)
    drift_summary = DriftDetectionSummary(**build_drift_detection_summary())
    latest_comparison_payload = build_workflow_model_diff(workflow_id) if workflow_id else None
    latest_comparison = ShadowPredictionComparison(**latest_comparison_payload) if latest_comparison_payload else None
    latest_evaluation_row = fetch_model_evaluation_runs(limit=1)
    latest_evaluation = build_model_evaluation_run_contract(latest_evaluation_row[0]) if latest_evaluation_row else None
    latest_feature_snapshot_hash = ""
    latest_model_input_hash = ""
    if workflow_id:
        workflow_predictions = fetch_workflow_predictions(workflow_id)
        active_prediction_row = next((row for row in reversed(workflow_predictions) if not bool(row["is_shadow_prediction"])), None)
        if active_prediction_row is not None:
            latest_feature_snapshot_hash = str(active_prediction_row["feature_snapshot_hash"] or "")
            latest_model_input_hash = str(active_prediction_row["model_input_hash"] or "")
    divergent = 0
    for row in rows:
        if (
            str(row["active_risk_band"]) != str(row["candidate_risk_band"])
            or str(row["active_predicted_specialty"]) != str(row["candidate_predicted_specialty"])
            or int(row["active_requires_review"]) != int(row["candidate_requires_review"])
        ):
            divergent += 1
    summary = build_governance_summary(
        active_model_key=str(active_model["model_key"]),
        candidate_model_key=str(candidate_model["model_key"]) if candidate_model else "",
        active_threshold_profile_id=active_threshold.id,
        candidate_threshold_profile_id=candidate_threshold.id if candidate_threshold else None,
        latest_feature_snapshot_hash=latest_feature_snapshot_hash,
        latest_model_input_hash=latest_model_input_hash,
        shadow_prediction_count=len(rows),
        divergent_shadow_predictions=divergent,
        latest_shadow_comparison=latest_comparison,
        latest_drift_summary=drift_summary,
        latest_evaluation_run_id=latest_evaluation.id if latest_evaluation else None,
        latest_evaluation_status=latest_evaluation.status if latest_evaluation else "",
        promotion_readiness=(latest_evaluation.promotion_recommendation if latest_evaluation else ""),
    )
    return model_dump(summary)


def fetch_workflow_lineage_summary(limit: int = 40) -> list[sqlite3.Row]:
    return replay_repository.fetch_workflow_lineage_summary(
        evaluation_prefix=EVALUATION_WORKFLOW_PREFIX,
        governance_prefix=GOVERNANCE_WORKFLOW_PREFIX,
        security_prefix=SECURITY_WORKFLOW_PREFIX,
        limit=limit,
    )


def build_workflow_replay_diff(workflow_a: str, workflow_b: str, limit: int = 60) -> dict[str, object]:
    replay_a = WorkflowReplay(**build_workflow_replay(workflow_a, limit=limit))
    replay_b = WorkflowReplay(**build_workflow_replay(workflow_b, limit=limit))
    differing_events: list[ReplayDiffDivergence] = []
    divergence_point: int | None = None
    max_len = max(len(replay_a.steps), len(replay_b.steps))

    for index in range(max_len):
        step_a = replay_a.steps[index] if index < len(replay_a.steps) else None
        step_b = replay_b.steps[index] if index < len(replay_b.steps) else None
        if step_a and step_b:
            same = (
                step_a.type == step_b.type
                and step_a.action == step_b.action
                and step_a.decision == step_b.decision
                and step_a.state == step_b.state
            )
            if same:
                continue
        if divergence_point is None:
            divergence_point = index
        differing_events.append(
            ReplayDiffDivergence(
                event_index=index,
                workflow_a_event_id=step_a.event_id if step_a else None,
                workflow_b_event_id=step_b.event_id if step_b else None,
                workflow_a_action=step_a.action if step_a else "missing",
                workflow_b_action=step_b.action if step_b else "missing",
                workflow_a_decision=step_a.decision if step_a else "missing",
                workflow_b_decision=step_b.decision if step_b else "missing",
                summary=(
                    f"{step_a.action if step_a else 'missing'} / {step_a.decision if step_a else 'missing'}"
                    f" diverged from {step_b.action if step_b else 'missing'} / {step_b.decision if step_b else 'missing'}"
                ),
            )
        )

    retry_count_a = sum(1 for step in replay_a.steps if step.type == EventType.RECOVERY_TRIGGERED)
    retry_count_b = sum(1 for step in replay_b.steps if step.type == EventType.RECOVERY_TRIGGERED)
    latency_a = sum(int(step.payload.get("latency_ms", 0)) for step in replay_a.steps)
    latency_b = sum(int(step.payload.get("latency_ms", 0)) for step in replay_b.steps)
    tool_names_a = [str(step.payload.get("tool_name", "")) for step in replay_a.steps if step.payload.get("tool_name")]
    tool_names_b = [str(step.payload.get("tool_name", "")) for step in replay_b.steps if step.payload.get("tool_name")]
    tool_outcome_delta = "matched"
    if tool_names_a != tool_names_b:
        tool_outcome_delta = f"{','.join(tool_names_a) or 'none'} vs {','.join(tool_names_b) or 'none'}"
    policy_path_delta = f"{replay_a.latest_decision} vs {replay_b.latest_decision}"
    summary = (
        "No divergence detected."
        if divergence_point is None
        else f"Divergence begins at step {divergence_point + 1}: {policy_path_delta}."
    )
    root_cause = synthesize_root_cause(
        replay_a,
        replay_b,
        divergence_point=divergence_point,
        retry_delta=retry_count_a - retry_count_b,
        latency_delta_ms=latency_a - latency_b,
        tool_outcome_delta=tool_outcome_delta,
        policy_path_delta=policy_path_delta,
    )
    diff = ReplayDiff(
        workflow_a=workflow_a,
        workflow_b=workflow_b,
        divergence_point=divergence_point,
        summary=summary,
        retry_delta=retry_count_a - retry_count_b,
        latency_delta_ms=latency_a - latency_b,
        policy_path_delta=policy_path_delta,
        tool_outcome_delta=tool_outcome_delta,
        root_cause=root_cause,
        differing_events=differing_events[:12],
    )
    return model_dump(diff)


def synthesize_root_cause(
    replay_a: WorkflowReplay,
    replay_b: WorkflowReplay,
    *,
    divergence_point: int | None,
    retry_delta: int,
    latency_delta_ms: int,
    tool_outcome_delta: str,
    policy_path_delta: str,
) -> RootCauseSummary | None:
    if divergence_point is None:
        return RootCauseSummary(
            probable_cause="No material divergence detected between the compared workflows.",
            confidence=96.0,
            supporting_events=[],
            divergence_point=None,
            affected_tools=[],
            retry_correlation="Retry patterns matched across both workflows.",
            incident_correlation="No incident-correlated divergence detected.",
        )

    step_a = replay_a.steps[divergence_point] if divergence_point < len(replay_a.steps) else None
    step_b = replay_b.steps[divergence_point] if divergence_point < len(replay_b.steps) else None
    affected_tools = sorted(
        {
            str(step.payload.get("tool_name", ""))
            for step in [step_a, step_b]
            if step and step.payload.get("tool_name")
        }
    )
    evidence: list[RootCauseEvidence] = []
    if step_a:
        evidence.append(
            RootCauseEvidence(
                timestamp=step_a.timestamp,
                signal="workflow_a_divergence",
                detail=f"{step_a.action} led to {step_a.decision} in {step_a.state}.",
            )
        )
    if step_b:
        evidence.append(
            RootCauseEvidence(
                timestamp=step_b.timestamp,
                signal="workflow_b_divergence",
                detail=f"{step_b.action} led to {step_b.decision} in {step_b.state}.",
            )
        )
    probable_cause = "Policy route divergence changed the workflow outcome."
    confidence = 74.0
    retry_correlation = "Retry behavior remained similar across both workflows."
    incident_correlation = "Divergence appears localized to workflow branching."

    if retry_delta != 0:
        probable_cause = "Recovery-chain behavior diverged after the initial execution path split."
        confidence = 82.0
        retry_correlation = f"Retry delta of {retry_delta} indicates asymmetric recovery pressure between the workflows."
        evidence.append(
            RootCauseEvidence(
                signal="retry_chain_delta",
                detail=f"Compared workflows differed by {retry_delta} recovery events.",
            )
        )
        incident_correlation = "Recovery pressure likely amplified downstream queue or escalation behavior."
    if affected_tools:
        probable_cause = f"Tool execution path diverged around {', '.join(affected_tools)}."
        confidence = max(confidence, 84.0)
        evidence.append(
            RootCauseEvidence(
                signal="tool_path_delta",
                detail=f"Compared workflows executed different tool paths: {tool_outcome_delta}.",
            )
        )
    if abs(latency_delta_ms) >= 250:
        probable_cause = "Latency imbalance likely triggered downstream policy or recovery divergence."
        confidence = max(confidence, 86.0)
        evidence.append(
            RootCauseEvidence(
                signal="latency_delta",
                detail=f"Latency delta measured at {latency_delta_ms}ms across the compared workflows.",
            )
        )
        incident_correlation = "Latency shift aligns with degraded orchestration timing."
    if "emergency_escalation" in policy_path_delta or "human_review" in policy_path_delta:
        probable_cause = "Policy escalation thresholds redirected one workflow into a higher-control branch."
        confidence = max(confidence, 88.0)
        evidence.append(
            RootCauseEvidence(
                signal="policy_path_delta",
                detail=f"Policy path delta observed as {policy_path_delta}.",
            )
        )

    return RootCauseSummary(
        probable_cause=probable_cause,
        confidence=confidence,
        supporting_events=evidence[:6],
        divergence_point=divergence_point,
        affected_tools=affected_tools,
        retry_correlation=retry_correlation,
        incident_correlation=incident_correlation,
    )


def record_automation_run(workflow_name: str, status: str, details: str, processed_count: int = 0) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO automation_runs (workflow_name, status, details, processed_count, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (workflow_name, status, details, processed_count, dt.datetime.now().isoformat(timespec="seconds")),
        )


def escalate_stale_reviews(max_age_hours: int = 2) -> int:
    cutoff = (dt.datetime.now() - dt.timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE appointments
            SET queue_state = 'priority-review', status = 'urgent-review'
            WHERE queue_state IN ('manual-review', 'assistant-review')
              AND status NOT IN ('cancelled', 'doctor-acknowledged')
              AND created_at <= ?
            """,
            (cutoff,),
        )
        return cursor.rowcount


def fetch_due_reminders(target_date: str) -> list[sqlite3.Row]:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT id AS appointment_id, patient_name, patient_email, phone, doctor_name,
                   appointment_date, slot_time, reminder_sent, status
            FROM appointments
            WHERE appointment_date = ?
              AND reminder_sent = 0
              AND status IN ('scheduled', 'doctor-acknowledged')
            ORDER BY slot_time ASC, created_at ASC
            """,
            (target_date,),
        ).fetchall()


def mark_reminder_delivery(appointment_id: int, delivery_status: str) -> None:
    normalized = (delivery_status or "").strip().lower()
    reminder_sent = 1 if normalized == "sent" else 0
    with get_connection() as connection:
        connection.execute(
            "UPDATE appointments SET reminder_sent = ? WHERE id = ?",
            (reminder_sent, appointment_id),
        )
