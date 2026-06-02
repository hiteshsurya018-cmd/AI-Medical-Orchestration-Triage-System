from __future__ import annotations

from .constants import SPECIALTY_LABELS, SYMPTOM_PATTERNS


DEPARTMENT_ROUTING_RULES: list[dict[str, object]] = [
    {
        "category": "Cardiac Symptoms",
        "specialty": "Cardiology",
        "symptoms": {"chest pain"},
        "phrases": {"chest pain", "chest tightness", "pressure in chest"},
    },
    {
        "category": "Orthopedic Injury",
        "specialty": "Orthopedics",
        "symptoms": {"broken bone", "joint pain"},
        "phrases": {"broken bone", "bone broke", "fracture", "joint pain", "knee pain", "fall", "sports injury"},
    },
    {
        "category": "Neurological Symptoms",
        "specialty": "Neurology",
        "symptoms": {"headache", "speech difficulty", "facial droop", "numbness", "weakness", "head injury"},
        "phrases": {"headache", "stroke", "slurred speech", "facial droop", "sudden weakness", "numbness", "head injury"},
    },
    {
        "category": "Skin Symptoms",
        "specialty": "Dermatology",
        "symptoms": {"rash"},
        "phrases": {"rash", "itching", "skin eruption"},
    },
    {
        "category": "ENT Symptoms",
        "specialty": "ENT",
        "symptoms": {"ear pain"},
        "phrases": {"ear pain", "earache", "sinus", "throat pain"},
    },
    {
        "category": "Eye Symptoms",
        "specialty": "Ophthalmology",
        "symptoms": {"eye pain"},
        "phrases": {"eye pain", "vision loss", "blurred vision", "eye problem"},
    },
    {
        "category": "Endocrine Symptoms",
        "specialty": "Endocrinology",
        "symptoms": {"diabetes concern"},
        "phrases": {"diabetes", "blood sugar", "high sugar"},
    },
    {
        "category": "Pregnancy Or Gynecology",
        "specialty": "Gynecology",
        "symptoms": {"pregnancy concern"},
        "phrases": {"pregnant", "pregnancy", "gynecology"},
    },
    {
        "category": "Child Illness",
        "specialty": "Pediatrics",
        "symptoms": {"child illness"},
        "phrases": {"child fever", "baby fever", "infant fever", "child illness"},
    },
    {
        "category": "Respiratory Symptoms",
        "specialty": "Pulmonology",
        "symptoms": {"breathing difficulty", "cough"},
        "phrases": {"shortness of breath", "breathing difficulty", "trouble breathing", "breathless", "cough"},
    },
    {
        "category": "Digestive Symptoms",
        "specialty": "Gastroenterology",
        "symptoms": {"stomach pain", "vomiting"},
        "phrases": {"stomach pain", "abdominal pain", "vomiting", "nausea"},
    },
]


def normalize_routing_specialty(raw_specialty: str) -> str:
    specialty = (raw_specialty or "").strip()
    if specialty in SPECIALTY_LABELS:
        return specialty
    lowered = specialty.lower()
    for known in SPECIALTY_LABELS:
        if known.lower() == lowered:
            return known
    return "General"


def extract_routing_symptoms(symptoms: str) -> list[str]:
    lowered = (symptoms or "").lower()
    extracted = [label for label, phrases in SYMPTOM_PATTERNS.items() if any(phrase in lowered for phrase in phrases)]
    return extracted


def classify_department(
    symptoms: str,
    *,
    extracted_symptoms: list[str] | None = None,
    fallback_specialty: str = "General",
) -> dict[str, object]:
    extracted = extracted_symptoms if extracted_symptoms is not None else extract_routing_symptoms(symptoms)
    lowered = (symptoms or "").lower()
    matched_candidates: list[dict[str, object]] = []
    for rule in DEPARTMENT_ROUTING_RULES:
        rule_symptoms = set(rule["symptoms"])
        rule_phrases = set(rule["phrases"])
        symptom_hits = sorted(rule_symptoms & set(extracted))
        phrase_hits = sorted(phrase for phrase in rule_phrases if phrase in lowered)
        score = (len(symptom_hits) * 35) + (len(phrase_hits) * 15)
        if score:
            matched_candidates.append({**rule, "matched_symptoms": symptom_hits, "matched_phrases": phrase_hits, "score": score})
    if matched_candidates:
        best = sorted(matched_candidates, key=lambda item: int(item["score"]), reverse=True)[0]
        specialty = normalize_routing_specialty(str(best["specialty"]))
        department = str(SPECIALTY_LABELS.get(specialty, SPECIALTY_LABELS["General"])["department"])
        confidence = min(99, 60 + int(best["score"]))
        return {
            "category": best["category"],
            "specialty": specialty,
            "department": department,
            "confidence": confidence,
            "matched_symptoms": best["matched_symptoms"],
            "matched_phrases": best["matched_phrases"],
            "routing_source": "department_classification_engine",
            "reason": f"Matched {', '.join(best['matched_symptoms'] or best['matched_phrases'])} to {department}.",
        }
    specialty = normalize_routing_specialty(fallback_specialty)
    department = str(SPECIALTY_LABELS.get(specialty, SPECIALTY_LABELS["General"])["department"])
    return {
        "category": "General Symptoms",
        "specialty": specialty,
        "department": department,
        "confidence": 50 if specialty != "General" else 35,
        "matched_symptoms": extracted[:4],
        "matched_phrases": [],
        "routing_source": "fallback",
        "reason": f"No deterministic department rule matched; routed to {department}.",
    }
