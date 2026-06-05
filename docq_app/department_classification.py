from __future__ import annotations

import os
import re
from typing import Any

from .constants import SPECIALTY_LABELS, SYMPTOM_PATTERNS


DEFAULT_PEDIATRIC_AGE_THRESHOLD = 16


EMERGENCY_RULES: list[dict[str, object]] = [
    {
        "rule_id": "emergency_stroke",
        "category": "Emergency Neurological Symptoms",
        "specialty": "Emergency",
        "phrases": {
            "stroke",
            "stroke symptoms",
            "facial droop",
            "slurred speech",
            "sudden weakness",
            "sudden numbness",
            "loss of consciousness",
        },
    },
    {
        "rule_id": "emergency_breathing",
        "category": "Emergency Breathing Difficulty",
        "specialty": "Emergency",
        "phrases": {
            "difficulty breathing",
            "severe breathing difficulty",
            "cannot breathe",
            "struggling to breathe",
            "severe shortness of breath",
        },
    },
    {
        "rule_id": "emergency_trauma_bleeding",
        "category": "Emergency Trauma Or Bleeding",
        "specialty": "Emergency",
        "phrases": {
            "severe trauma",
            "major trauma",
            "road accident",
            "heavy bleeding",
            "severe bleeding",
            "uncontrolled bleeding",
        },
    },
]


SPECIALTY_RULES: list[dict[str, object]] = [
    {
        "rule_id": "cardiology_core",
        "category": "Cardiac Symptoms",
        "specialty": "Cardiology",
        "phrases": {"chest pain", "palpitations", "irregular heartbeat", "heart racing", "pressure in chest", "chest tightness"},
    },
    {
        "rule_id": "neurology_core",
        "category": "Neurological Symptoms",
        "specialty": "Neurology",
        "phrases": {"seizure", "seizures", "numbness", "persistent neurological symptoms", "nerve weakness", "migraine with aura"},
    },
    {
        "rule_id": "orthopedics_core",
        "category": "Orthopedic Injury",
        "specialty": "Orthopedics",
        "phrases": {
            "fracture",
            "broken bone",
            "broken leg",
            "broken arm",
            "bone broke",
            "joint injury",
            "musculoskeletal trauma",
            "sports injury",
            "knee injury",
        },
    },
    {
        "rule_id": "dermatology_core",
        "category": "Skin Symptoms",
        "specialty": "Dermatology",
        "phrases": {"rash", "skin rash", "acne", "skin infection", "itching", "skin lesions", "skin lesion", "eczema"},
    },
    {
        "rule_id": "ent_core",
        "category": "ENT Symptoms",
        "specialty": "ENT",
        "phrases": {"ear pain", "earache", "hearing problem", "hearing problems", "nose problem", "nose problems", "throat condition", "throat conditions", "sinus"},
    },
    {
        "rule_id": "pulmonology_core",
        "category": "Respiratory Symptoms",
        "specialty": "Pulmonology",
        "phrases": {"asthma", "chronic breathing issue", "chronic breathing issues", "respiratory condition", "respiratory conditions", "wheezing"},
    },
    {
        "rule_id": "psychiatry_core",
        "category": "Mental Health Symptoms",
        "specialty": "Psychiatry",
        "phrases": {"depression", "anxiety", "panic attacks", "mental health concern", "mental health concerns"},
    },
    {
        "rule_id": "gynecology_core",
        "category": "Gynecology Symptoms",
        "specialty": "Gynecology",
        "phrases": {"pregnancy", "pregnant", "menstrual issue", "menstrual issues", "women's health", "womens health", "gynecology"},
    },
]


GENERAL_MEDICINE_PHRASES = {
    "fever",
    "high temperature",
    "viral fever",
    "body pain",
    "fatigue",
    "weakness",
    "dizziness",
    "cold",
    "cough",
    "flu",
    "sore throat",
    "stomach pain",
    "acidity",
    "gas",
    "vomiting",
    "nausea",
    "loss of appetite",
    "feeling unwell",
    "general sickness",
    "general health concern",
    "unknown symptoms",
    "general illness",
}


PEDIATRIC_GENERAL_PHRASES = GENERAL_MEDICINE_PHRASES | {
    "child fever",
    "baby fever",
    "infant fever",
    "child illness",
}


def pediatric_age_threshold() -> int:
    raw_value = os.getenv("DOCQ_PEDIATRIC_AGE_THRESHOLD", str(DEFAULT_PEDIATRIC_AGE_THRESHOLD))
    try:
        return max(1, int(raw_value))
    except (TypeError, ValueError):
        return DEFAULT_PEDIATRIC_AGE_THRESHOLD


def normalize_routing_specialty(raw_specialty: str) -> str:
    specialty = (raw_specialty or "").strip()
    if specialty in SPECIALTY_LABELS:
        return specialty
    lowered = specialty.lower()
    for known in SPECIALTY_LABELS:
        if known.lower() == lowered:
            return known
    return "General"


def _phrase_matches(text: str, phrases: set[str]) -> list[str]:
    matches = []
    for phrase in sorted(phrases, key=len, reverse=True):
        normalized = re.escape(phrase.lower()).replace("\\ ", r"\s+")
        if re.search(rf"(?<![a-z0-9]){normalized}(?![a-z0-9])", text):
            matches.append(phrase)
    return matches


def extract_routing_symptoms(symptoms: str) -> list[str]:
    lowered = (symptoms or "").lower()
    extracted = [label for label, phrases in SYMPTOM_PATTERNS.items() if any(phrase in lowered for phrase in phrases)]
    general_hits = _phrase_matches(lowered, GENERAL_MEDICINE_PHRASES)
    specialty_hits: list[str] = []
    for rule in EMERGENCY_RULES + SPECIALTY_RULES:
        specialty_hits.extend(_phrase_matches(lowered, set(rule["phrases"])))
    combined = []
    for item in extracted + specialty_hits + general_hits:
        if item not in combined:
            combined.append(item)
    return combined


def _department_payload(
    *,
    rule: dict[str, object] | None,
    specialty: str,
    category: str,
    confidence: int,
    matched_keywords: list[str],
    matched_rules: list[str],
    routing_source: str,
    reason: str,
) -> dict[str, object]:
    normalized_specialty = normalize_routing_specialty(specialty)
    department = str(SPECIALTY_LABELS.get(normalized_specialty, SPECIALTY_LABELS["General"])["department"])
    return {
        "category": category,
        "specialty": normalized_specialty,
        "department": department,
        "selected_department": department,
        "confidence": confidence,
        "confidence_score": confidence,
        "matched_symptoms": matched_keywords,
        "matched_phrases": matched_keywords,
        "matched_keywords": matched_keywords,
        "matched_rules": matched_rules,
        "routing_rule": str(rule.get("rule_id")) if rule else "",
        "routing_source": routing_source,
        "reason": reason,
        "audit": {
            "matched_keywords": matched_keywords,
            "matched_rules": matched_rules,
            "confidence_score": confidence,
            "selected_department": department,
        },
    }


def _best_rule_match(lowered: str, rules: list[dict[str, object]]) -> dict[str, Any] | None:
    candidates = []
    for rule in rules:
        matched = _phrase_matches(lowered, set(rule["phrases"]))
        if matched:
            candidates.append({**rule, "matched_keywords": matched, "score": (len(matched) * 35) + min(20, max(len(item) for item in matched))})
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: int(item["score"]), reverse=True)[0]


def classify_department(
    symptoms: str,
    *,
    extracted_symptoms: list[str] | None = None,
    fallback_specialty: str = "General",
    patient_age: int | None = None,
    pediatric_threshold: int | None = None,
) -> dict[str, object]:
    lowered = (symptoms or "").lower()
    extracted = extracted_symptoms if extracted_symptoms is not None else extract_routing_symptoms(symptoms)
    extracted_lowered = " ".join(str(item).lower() for item in extracted)
    searchable = f"{lowered} {extracted_lowered}".strip()
    threshold = pediatric_threshold or pediatric_age_threshold()

    emergency = _best_rule_match(searchable, EMERGENCY_RULES)
    if emergency:
        matched = list(emergency["matched_keywords"])
        return _department_payload(
            rule=emergency,
            specialty=str(emergency["specialty"]),
            category=str(emergency["category"]),
            confidence=99,
            matched_keywords=matched,
            matched_rules=[str(emergency["rule_id"])],
            routing_source="emergency_department_rule",
            reason=f"Emergency override matched {', '.join(matched)}; routed to Emergency Department.",
        )

    specialty = _best_rule_match(searchable, SPECIALTY_RULES)
    if specialty:
        matched = list(specialty["matched_keywords"])
        department = str(SPECIALTY_LABELS.get(str(specialty["specialty"]), SPECIALTY_LABELS["General"])["department"])
        return _department_payload(
            rule=specialty,
            specialty=str(specialty["specialty"]),
            category=str(specialty["category"]),
            confidence=min(98, 78 + (len(matched) * 7)),
            matched_keywords=matched,
            matched_rules=[str(specialty["rule_id"])],
            routing_source="department_classification_engine",
            reason=f"Matched {', '.join(matched)} to {department}.",
        )

    general_matches = _phrase_matches(searchable, PEDIATRIC_GENERAL_PHRASES)
    is_pediatric = patient_age is not None and patient_age < threshold
    if is_pediatric and general_matches:
        return _department_payload(
            rule=None,
            specialty="Pediatrics",
            category="Pediatric General Illness",
            confidence=88,
            matched_keywords=general_matches,
            matched_rules=["pediatric_age_general_symptom_rule"],
            routing_source="pediatric_age_rule",
            reason=f"Patient age {patient_age} is below pediatric threshold {threshold}; general symptoms routed to Pediatrics.",
        )

    if general_matches:
        return _department_payload(
            rule=None,
            specialty="General",
            category="General Medicine Symptoms",
            confidence=84,
            matched_keywords=general_matches,
            matched_rules=["general_medicine_symptom_rule"],
            routing_source="general_medicine_rule",
            reason=f"Matched {', '.join(general_matches)} to General Medicine.",
        )

    fallback = normalize_routing_specialty(fallback_specialty)
    if fallback not in {"General", "Pediatrics"}:
        fallback = "General"
    if patient_age is not None and patient_age < threshold:
        fallback = "Pediatrics"
    department = str(SPECIALTY_LABELS.get(fallback, SPECIALTY_LABELS["General"])["department"])
    return _department_payload(
        rule=None,
        specialty=fallback,
        category="General Symptoms",
        confidence=45 if fallback == "General" else 62,
        matched_keywords=extracted[:4],
        matched_rules=["low_confidence_general_fallback"],
        routing_source="general_medicine_fallback",
        reason=f"No high-confidence deterministic rule matched; routed to {department}.",
    )
