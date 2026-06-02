from __future__ import annotations

from typing import Any

from .ml import extract_symptoms


QUESTIONNAIRES: dict[str, dict[str, object]] = {
    "chest_pain": {
        "trigger_symptoms": {"chest pain"},
        "label": "Chest Pain",
        "questions": [
            {"id": "pain_location", "text": "Where is the chest pain located?"},
            {"id": "pain_severity", "text": "Rate the pain from 1 to 10."},
            {"id": "radiation", "text": "Does the pain spread to the left arm, jaw, back, or shoulder?"},
            {"id": "breathing_difficulty", "text": "Are you having shortness of breath or trouble breathing?"},
            {"id": "sweating", "text": "Are you sweating, feeling faint, or unusually weak?"},
            {"id": "duration", "text": "How long has this chest pain been present?"},
        ],
    },
    "breathing_difficulty": {
        "trigger_symptoms": {"breathing difficulty"},
        "label": "Breathing Difficulty",
        "questions": [
            {"id": "onset", "text": "Did the breathing difficulty start suddenly or gradually?"},
            {"id": "severity", "text": "Can you speak full sentences without stopping for breath?"},
            {"id": "chest_pain", "text": "Is there any chest pain along with the breathing difficulty?"},
            {"id": "wheezing", "text": "Are you wheezing or do you have asthma history?"},
        ],
    },
    "fever": {
        "trigger_symptoms": {"fever"},
        "label": "Fever",
        "questions": [
            {"id": "temperature", "text": "What is the highest temperature recorded?"},
            {"id": "duration", "text": "How many days has the fever been present?"},
            {"id": "breathing", "text": "Any breathing difficulty, chest pain, or confusion?"},
            {"id": "hydration", "text": "Are you able to drink fluids and pass urine normally?"},
        ],
    },
    "dizziness": {
        "trigger_symptoms": {"dizziness"},
        "label": "Dizziness",
        "questions": [
            {"id": "fainting", "text": "Did you faint or nearly faint?"},
            {"id": "weakness", "text": "Any one-sided weakness, facial droop, or speech difficulty?"},
            {"id": "chest_pain", "text": "Any chest pain, palpitations, or breathing difficulty?"},
            {"id": "duration", "text": "How long has the dizziness been present?"},
        ],
    },
    "orthopedic_trauma": {
        "trigger_symptoms": {"broken bone", "joint pain"},
        "label": "Injury Assessment",
        "questions": [
            {"id": "can_stand", "text": "Can you stand or move the affected limb?"},
            {"id": "visible_deformity", "text": "Is the limb visibly deformed or out of normal position?"},
            {"id": "major_trauma", "text": "Was there a fall, road accident, or major impact?"},
            {"id": "severe_bleeding", "text": "Is there severe bleeding or an open wound?"},
            {"id": "pain_severity", "text": "Rate the pain from 1 to 10."},
        ],
    },
    "neurological_red_flags": {
        "trigger_symptoms": {"speech difficulty", "facial droop", "numbness", "weakness"},
        "label": "Neurological Red Flags",
        "questions": [
            {"id": "sudden_onset", "text": "Did the symptoms start suddenly?"},
            {"id": "one_sided_weakness", "text": "Is there weakness or numbness on one side of the body?"},
            {"id": "speech_difficulty", "text": "Is speech slurred or difficult?"},
            {"id": "facial_droop", "text": "Is there facial drooping?"},
            {"id": "duration", "text": "When did these symptoms start?"},
        ],
    },
    "trauma": {
        "trigger_symptoms": {"severe bleeding", "head injury"},
        "label": "Trauma Assessment",
        "questions": [
            {"id": "mechanism", "text": "Was this from a road accident, fall, or direct injury?"},
            {"id": "bleeding", "text": "Is there heavy bleeding?"},
            {"id": "head_injury", "text": "Was there a head injury, loss of consciousness, or vomiting?"},
            {"id": "pain_severity", "text": "Rate the pain from 1 to 10."},
        ],
    },
}


def select_questionnaire(symptoms: str) -> dict[str, object] | None:
    extracted = set(extract_symptoms(symptoms))
    for questionnaire_id, questionnaire in QUESTIONNAIRES.items():
        triggers = set(questionnaire["trigger_symptoms"])
        if extracted & triggers:
            return {
                "id": questionnaire_id,
                "label": questionnaire["label"],
                "questions": list(questionnaire["questions"]),
            }
    return None


def next_question(questionnaire: dict[str, Any], answers: dict[str, str] | None = None) -> dict[str, str] | None:
    answers = answers or {}
    for question in questionnaire.get("questions", []):
        question_id = str(question["id"])
        if question_id not in answers:
            return {"id": question_id, "text": str(question["text"])}
    return None


def format_questionnaire_context(questionnaire_payload: dict[str, Any] | None) -> str:
    if not questionnaire_payload:
        return ""
    label = str(questionnaire_payload.get("label") or "Clinical questionnaire")
    answers = questionnaire_payload.get("answers") or {}
    if not isinstance(answers, dict) or not answers:
        return ""
    parts = [f"{key.replace('_', ' ')}: {value}" for key, value in answers.items() if str(value).strip()]
    return f"{label} answers - " + "; ".join(parts) if parts else ""
