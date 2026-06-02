from __future__ import annotations

from typing import Any


def detect_intent(message: str, *, awaiting_age: bool = False) -> str:
    lowered = (message or "").strip().lower()
    if awaiting_age:
        return "provide_age"
    if any(term in lowered for term in {"book", "appointment", "slot", "schedule"}):
        return "confirm_booking"
    if any(term in lowered for term in {"follow up", "follow-up"}):
        return "request_followup"
    if any(term in lowered for term in {"cancel"}):
        return "cancel_appointment"
    return "report_symptom"


def build_workflow_state(analysis: dict[str, Any]) -> dict[str, Any]:
    booking_stage = "review_required" if analysis.get("requires_review") else "slot_selection"
    if analysis.get("urgency") == "Emergency":
        booking_stage = "fast_track_review"
    return {
        "booking_stage": booking_stage,
        "confirmation_sent": False,
        "requires_followup_question": False,
        "care_path": "accelerated" if analysis.get("booking_mode") == "urgent" else "standard",
    }


def build_ui_actions(analysis: dict[str, Any]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    available_dates = analysis.get("available_dates") or []
    doctor_matches = analysis.get("doctor_matches") or []
    if available_dates:
        actions.append({"type": "button", "action": "book_recommended_slot", "label": "Choose Appointment Time"})
        if len(available_dates) > 1:
            actions.append({"type": "button", "action": "show_more_slots", "label": "View More Times"})
    if len(doctor_matches) > 1:
        actions.append({"type": "button", "action": "change_doctor", "label": "Choose Doctor"})
    if analysis.get("requires_review"):
        actions.append({"type": "status", "action": "review_required", "label": "Clinical review advised"})
    return actions


def build_patient_response(payload: dict[str, Any]) -> str:
    urgency = payload.get("urgency")
    department = payload.get("department") or payload.get("specialty") or "the right care team"
    next_slot = payload.get("next_slot", "No live slot available")
    requires_review = bool(payload.get("requires_review"))

    if urgency == "Emergency":
        return (
            "Your symptoms require urgent medical attention. We recommend seeking immediate emergency care. "
            "You can contact emergency services, notify a trusted contact, or review nearby-care options."
        )
    if payload.get("booking_mode") == "urgent":
        return (
            f"Your symptoms may benefit from prompt review by {department}. "
            f"You can choose a doctor or request the earliest available appointment: {next_slot}."
        )
    if requires_review:
        return f"You may benefit from consulting {department}. Choose a doctor below, or let DOCQ request the earliest available review slot."
    return f"You may benefit from consulting {department}. Choose a doctor below, or let DOCQ recommend the earliest available appointment."


def build_conversation_payload(
    *,
    conversation_id: str,
    patient_id: str | None,
    intent: str,
    symptoms: str,
    analysis: dict[str, Any],
) -> dict[str, Any]:
    recommended_doctor = {
        "doctor_name": analysis.get("doctor_name"),
        "branch": analysis.get("branch"),
        "continuity_reason": analysis.get("continuity_reason"),
    }
    payload = {
        "conversation_id": conversation_id,
        "patient_id": patient_id,
        "intent": intent,
        "symptoms": analysis.get("extracted_symptoms") or [symptoms],
        "raw_message": symptoms,
        "department": analysis.get("department"),
        "department_category": analysis.get("department_category"),
        "department_confidence": analysis.get("department_confidence"),
        "department_routing_reason": analysis.get("department_routing_reason"),
        "department_routing_source": analysis.get("department_routing_source"),
        "urgency": analysis.get("urgency"),
        "requires_review": bool(analysis.get("requires_review")),
        "recommended_action": analysis.get("recommended_action"),
        "doctor_matches": analysis.get("doctor_matches") or [],
        "recommended_doctor": recommended_doctor,
        "available_slots": analysis.get("available_dates") or [],
        "patient_memory": analysis.get("known_context") or {},
        "workflow_state": build_workflow_state(analysis),
        "ui_actions": build_ui_actions(analysis),
        "automation_tasks": [],
    }
    payload["patient_message"] = build_patient_response({**analysis, **payload})
    return payload
