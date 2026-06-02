from __future__ import annotations

import logging
import pickle
import re

import pandas as pd
from flask import current_app
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from .constants import CONDITION_RISK_PATTERNS, DEFAULT_SPECIALTY, EMERGENCY_KEYWORDS, HIGH_SEVERITY_KEYWORDS, LOW_CONFIDENCE_THRESHOLD, QUICK_AID_RULES, REVIEW_CONFIDENCE_THRESHOLD, SPECIALTY_LABELS, SYMPTOM_PATTERNS, URGENT_KEYWORDS
from .department_classification import classify_department

logger = logging.getLogger(__name__)

SPECIALTY_HINTS = {
    "Cardiology": {"chest pain", "dizziness", "weakness"},
    "Pulmonology": {"breathing difficulty", "cough", "fever"},
    "Neurology": {"headache", "numbness", "dizziness", "weakness", "speech difficulty", "facial droop"},
    "Orthopedics": {"joint pain", "broken bone", "weakness"},
    "Dermatology": {"rash"},
    "Gastroenterology": {"stomach pain", "vomiting", "fever"},
    "ENT": {"ear pain"},
    "Ophthalmology": {"eye pain"},
    "Endocrinology": {"diabetes concern"},
    "Gynecology": {"pregnancy concern"},
    "Pediatrics": {"child illness"},
    "General": {"fever", "weakness", "cough"},
}

SYMPTOM_SPECIALTY_OVERRIDES = {
    "chest pain": "Cardiology",
    "broken bone": "Orthopedics",
    "severe bleeding": "General",
    "head injury": "Neurology",
    "speech difficulty": "Neurology",
    "facial droop": "Neurology",
    "breathing difficulty": "Pulmonology",
    "eye pain": "Ophthalmology",
    "ear pain": "ENT",
    "diabetes concern": "Endocrinology",
    "pregnancy concern": "Gynecology",
    "child illness": "Pediatrics",
}


def _max_probability(probabilities) -> float:
    if hasattr(probabilities, "max"):
        return float(probabilities.max())
    return max(max(row) for row in probabilities)


def build_pipeline(max_iter: int = 320) -> Pipeline:
    return Pipeline(
        [
            ("vectorizer", TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=2)),
            ("classifier", LogisticRegression(solver="lbfgs", max_iter=max_iter, class_weight="balanced")),
        ]
    )


def train_models() -> tuple[Pipeline, Pipeline]:
    dataset = pd.read_csv(current_app.config["DATASET_PATH"])
    dataset["Cleaned_text"] = dataset["Cleaned_text"].fillna("")
    dataset["Health"] = dataset["Health"].fillna("")
    dataset["Category"] = dataset["Category"].fillna("Unknown")

    cat_x_train, _, cat_y_train, _ = train_test_split(
        dataset["Cleaned_text"], dataset["Category"], test_size=0.2, random_state=42, stratify=dataset["Category"]
    )
    health_subset = dataset[dataset["Health"].str.strip() != ""].copy()
    health_x_train, _, health_y_train, _ = train_test_split(
        health_subset["Cleaned_text"], health_subset["Health"], test_size=0.2, random_state=42, stratify=health_subset["Health"]
    )

    category_model = build_pipeline()
    category_model.fit(cat_x_train, cat_y_train)
    health_model = build_pipeline()
    health_model.fit(health_x_train, health_y_train)

    model_dir = current_app.config["MODEL_DIR"]
    model_dir.mkdir(exist_ok=True)
    with (model_dir / "category_model.pkl").open("wb") as file_obj:
        pickle.dump(category_model, file_obj)
    with (model_dir / "health_model.pkl").open("wb") as file_obj:
        pickle.dump(health_model, file_obj)
    return category_model, health_model


def load_models() -> tuple[Pipeline, Pipeline]:
    category_path = current_app.config["MODEL_DIR"] / "category_model.pkl"
    health_path = current_app.config["MODEL_DIR"] / "health_model.pkl"
    if category_path.exists() and health_path.exists():
        with category_path.open("rb") as file_obj:
            category_model = pickle.load(file_obj)
        with health_path.open("rb") as file_obj:
            health_model = pickle.load(file_obj)
        return category_model, health_model
    logger.info("Model files not found; training new models.")
    return train_models()


def init_models(load_on_startup: bool) -> None:
    if load_on_startup:
        current_app.extensions["docq_models"] = load_models()
    else:
        current_app.extensions["docq_models"] = (None, None)


def set_models(category_model, health_model) -> None:
    current_app.extensions["docq_models"] = (category_model, health_model)


def normalize_specialty(raw_specialty: str) -> str:
    specialty = (raw_specialty or "").strip()
    if specialty in SPECIALTY_LABELS:
        return specialty
    lowered = specialty.lower()
    for known in SPECIALTY_LABELS:
        if known.lower() == lowered:
            return known
    return "General"


def extract_symptoms(symptoms: str) -> list[str]:
    lowered = symptoms.lower()
    extracted = [label for label, phrases in SYMPTOM_PATTERNS.items() if any(phrase in lowered for phrase in phrases)]
    if extracted:
        return extracted
    fallback = re.findall(r"\b[a-z]{4,}\b", lowered)
    return fallback[:4]


def detect_history_flags(medical_history: str) -> list[str]:
    lowered = (medical_history or "").lower()
    return [label for label, phrases in CONDITION_RISK_PATTERNS.items() if any(phrase in lowered for phrase in phrases)]


def classify_severity(symptoms: str, patient_age: int | None = None, history_flags: list[str] | None = None) -> str:
    lowered = symptoms.lower()
    history_flags = history_flags or []
    if any(keyword in lowered for keyword in EMERGENCY_KEYWORDS):
        return "Emergency"
    if any(term in lowered for term in HIGH_SEVERITY_KEYWORDS):
        return "High"
    if patient_age is not None and (patient_age >= 70 or patient_age <= 5) and history_flags:
        return "High"
    if any(term in lowered for term in {"pain", "swelling", "infection", "injury", "fever", "vomiting"}):
        return "Moderate"
    return "Low"


def detect_urgency(symptoms: str, severity: str | None = None) -> str:
    if severity == "Emergency":
        return "Emergency"
    if severity == "High":
        return "High"
    lowered = symptoms.lower()
    if any(keyword in lowered for keyword in URGENT_KEYWORDS):
        return "High"
    if any(term in lowered for term in {"pain", "swelling", "persistent", "infection", "injury", "fever"}):
        return "Moderate"
    return "Low"


def determine_queue_state(urgency: str, confidence: float) -> str:
    if urgency in {"High", "Emergency"}:
        return "priority-review"
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        return "manual-review"
    if confidence < REVIEW_CONFIDENCE_THRESHOLD:
        return "assistant-review"
    return "awaiting-doctor"


def compute_age_risk(patient_age: int | None) -> float:
    if patient_age is None:
        return 0.0
    if patient_age >= 75 or patient_age <= 2:
        return 30.0
    if patient_age >= 65 or patient_age <= 5:
        return 24.0
    if patient_age >= 50 or patient_age <= 12:
        return 16.0
    return 6.0


def compute_history_risk(history_flags: list[str]) -> float:
    if not history_flags:
        return 0.0
    return min(28.0, 10.0 + (len(history_flags) * 6.0))


def build_specialty_confidence_map(primary_specialty: str, extracted_symptoms: list[str], base_confidence: float) -> list[dict[str, object]]:
    scores: dict[str, float] = {}
    for specialty, hints in SPECIALTY_HINTS.items():
        overlap = len(set(extracted_symptoms) & hints)
        if overlap:
            scores[specialty] = float(overlap * 18)
    scores[primary_specialty] = max(scores.get(primary_specialty, 0.0), max(base_confidence, 55.0))
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:3]
    return [{"specialty": specialty, "confidence": round(min(score, 99.0), 1)} for specialty, score in ranked]


def specialty_from_symptoms(extracted_symptoms: list[str], fallback: str = "General") -> str:
    classification = classify_department("", extracted_symptoms=extracted_symptoms, fallback_specialty=fallback)
    if classification["routing_source"] == "department_classification_engine":
        return str(classification["specialty"])
    for symptom in extracted_symptoms:
        if symptom in SYMPTOM_SPECIALTY_OVERRIDES:
            return SYMPTOM_SPECIALTY_OVERRIDES[symptom]
    return fallback


def build_quick_aid(extracted_symptoms: list[str], severity: str) -> list[str]:
    advice = []
    for rule in QUICK_AID_RULES:
        if any(trigger in extracted_symptoms for trigger in rule["triggers"]):
            advice.append(rule["advice"])
    if severity == "Emergency":
        advice.append("Do not wait for a routine slot if symptoms are escalating; activate emergency support immediately.")
    if not advice:
        advice.append("Rest, monitor the symptoms closely, and bring current medications and prior records to the consultation.")
    return advice[:3]


def build_triage_summary(department: str, severity: str, extracted_symptoms: list[str], history_flags: list[str], priority_score: float) -> str:
    symptom_text = ", ".join(extracted_symptoms[:4]) if extracted_symptoms else "general symptoms"
    history_text = f" History risks: {', '.join(history_flags)}." if history_flags else ""
    return (
        f"DOCQ extracted {symptom_text}, assessed {severity.lower()} severity, "
        f"and routed the case to {department}.{history_text}"
    )


def analyze_symptoms(symptoms: str, patient_age: int | None = None, medical_history: str = "") -> dict[str, object]:
    cleaned = (symptoms or "").strip()
    history_flags = detect_history_flags(medical_history)
    if not cleaned:
        return {
            "category": "Unknown",
            "specialty": "General",
            "department": DEFAULT_SPECIALTY["department"],
            "doctor_name": DEFAULT_SPECIALTY["doctor"],
            "branch": DEFAULT_SPECIALTY["branch"],
            "confidence": 0.0,
            "severity": "Low",
            "urgency": "Low",
            "priority_score": 0.0,
            "age_risk": compute_age_risk(patient_age),
            "history_flags": history_flags,
            "extracted_symptoms": [],
            "specialty_matches": [{"specialty": "General", "confidence": 0.0}],
            "quick_aid": ["Share the symptoms clearly and bring any current prescriptions or reports."],
            "queue_state": "manual-review",
            "requires_review": True,
            "slot_note": DEFAULT_SPECIALTY["slot_note"],
            "recommended_action": "Needs intake clarification",
            "summary": "Share the symptoms and DOCQ will recommend the right team and next step.",
            "triage_summary": "DOCQ needs symptom details before clinical routing can be completed.",
        }

    category_model, health_model = current_app.extensions.get("docq_models", (None, None))
    extracted_symptoms = extract_symptoms(cleaned)
    severity = classify_severity(cleaned, patient_age, history_flags)
    age_risk = compute_age_risk(patient_age)
    history_risk = compute_history_risk(history_flags)
    if not category_model or not health_model:
        logger.warning("Models were not initialized; falling back to general routing.")
        confidence_pct = 0.0
        department_classification = classify_department(cleaned, extracted_symptoms=extracted_symptoms, fallback_specialty="General")
        specialty = str(department_classification["specialty"])
        doctor_info = SPECIALTY_LABELS.get(specialty, DEFAULT_SPECIALTY)
        urgency = detect_urgency(cleaned, severity)
        queue_state = determine_queue_state(urgency, confidence_pct)
        severity_score = {"Low": 25.0, "Moderate": 55.0, "High": 80.0, "Emergency": 100.0}[severity]
        priority_score = round((severity_score * 0.5) + (age_risk * 0.2) + (history_risk * 0.2), 1)
        quick_aid = build_quick_aid(extracted_symptoms, severity)
        return {
            "category": "Unknown",
            "specialty": specialty,
            "department": doctor_info["department"],
            "department_category": department_classification["category"],
            "department_confidence": department_classification["confidence"],
            "department_routing_reason": department_classification["reason"],
            "department_routing_source": department_classification["routing_source"],
            "doctor_name": doctor_info["doctor"],
            "branch": doctor_info["branch"],
            "confidence": confidence_pct,
            "severity": severity,
            "urgency": urgency,
            "priority_score": priority_score,
            "age_risk": age_risk,
            "history_flags": history_flags,
            "extracted_symptoms": extracted_symptoms,
            "specialty_matches": [{"specialty": specialty, "confidence": max(confidence_pct, float(department_classification["confidence"]))}],
            "quick_aid": quick_aid,
            "queue_state": queue_state,
            "requires_review": True,
            "slot_note": doctor_info["slot_note"],
            "recommended_action": "Immediate escalation recommended" if urgency == "Emergency" else "Clinical review before booking",
            "summary": f"DOCQ recommends {doctor_info['department']} for first review and will guide the next care step based on urgency.",
            "triage_summary": build_triage_summary(doctor_info["department"], severity, extracted_symptoms, history_flags, priority_score),
        }

    category_prediction = category_model.predict([cleaned])[0]
    category_confidence = _max_probability(category_model.predict_proba([cleaned]))
    if str(category_prediction).lower() == "medical":
        predicted_specialty = health_model.predict([cleaned])[0]
        confidence = _max_probability(health_model.predict_proba([cleaned]))
    else:
        predicted_specialty = "General"
        confidence = category_confidence

    department_classification = classify_department(cleaned, extracted_symptoms=extracted_symptoms, fallback_specialty=normalize_specialty(str(predicted_specialty)))
    specialty = str(department_classification["specialty"])
    doctor_info = SPECIALTY_LABELS.get(specialty, DEFAULT_SPECIALTY)
    urgency = detect_urgency(cleaned, severity)
    confidence_pct = round(confidence * 100, 1)
    queue_state = determine_queue_state(urgency, confidence_pct)
    requires_review = queue_state in {"manual-review", "assistant-review", "priority-review"}
    severity_score = {"Low": 25.0, "Moderate": 55.0, "High": 80.0, "Emergency": 100.0}[severity]
    priority_score = round((severity_score * 0.5) + (age_risk * 0.2) + (history_risk * 0.2) + (min(confidence_pct, 100.0) * 0.1), 1)
    specialty_matches = build_specialty_confidence_map(specialty, extracted_symptoms, confidence_pct)
    quick_aid = build_quick_aid(extracted_symptoms, severity)
    recommended_action = "Immediate escalation recommended" if urgency == "Emergency" else (
        "Clinical review before booking" if requires_review else "Proceed to booking"
    )
    return {
        "category": str(category_prediction),
        "specialty": specialty,
        "department": doctor_info["department"],
        "department_category": department_classification["category"],
        "department_confidence": department_classification["confidence"],
        "department_routing_reason": department_classification["reason"],
        "department_routing_source": department_classification["routing_source"],
        "doctor_name": doctor_info["doctor"],
        "branch": doctor_info["branch"],
        "confidence": confidence_pct,
        "severity": severity,
        "urgency": urgency,
        "priority_score": priority_score,
        "age_risk": age_risk,
        "history_flags": history_flags,
        "extracted_symptoms": extracted_symptoms,
        "specialty_matches": specialty_matches,
        "quick_aid": quick_aid,
        "queue_state": queue_state,
        "requires_review": requires_review,
        "slot_note": doctor_info["slot_note"],
        "recommended_action": recommended_action,
        "summary": f"DOCQ recommends {doctor_info['department']} and will help you choose the next appropriate care step.",
        "triage_summary": build_triage_summary(doctor_info["department"], severity, extracted_symptoms, history_flags, priority_score),
    }
