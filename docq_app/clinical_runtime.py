from __future__ import annotations

import re
from typing import Any


VITAL_THRESHOLDS = {
    "spo2_critical_lt": 90.0,
    "systolic_critical_gt": 180.0,
    "diastolic_critical_gt": 120.0,
    "heart_rate_urgent_gt": 140.0,
    "temperature_urgent_gt_f": 104.0,
}


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, int | float):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def normalize_vitals(payload: dict[str, Any] | None) -> dict[str, float | str | None]:
    payload = payload or {}
    blood_pressure = str(payload.get("blood_pressure") or payload.get("bp") or "").strip()
    systolic = _number(payload.get("systolic_bp"))
    diastolic = _number(payload.get("diastolic_bp"))
    if blood_pressure and (systolic is None or diastolic is None):
        parts = re.findall(r"\d+(?:\.\d+)?", blood_pressure)
        if len(parts) >= 2:
            systolic = float(parts[0])
            diastolic = float(parts[1])
    return {
        "blood_pressure": blood_pressure or (f"{int(systolic)}/{int(diastolic)}" if systolic and diastolic else ""),
        "systolic_bp": systolic,
        "diastolic_bp": diastolic,
        "heart_rate": _number(payload.get("heart_rate") or payload.get("hr")),
        "respiratory_rate": _number(payload.get("respiratory_rate") or payload.get("rr")),
        "spo2": _number(payload.get("spo2") or payload.get("oxygen_saturation")),
        "temperature_f": _number(payload.get("temperature_f") or payload.get("temperature")),
        "height_cm": _number(payload.get("height_cm") or payload.get("height")),
        "weight_kg": _number(payload.get("weight_kg") or payload.get("weight")),
    }


def evaluate_vitals(vitals: dict[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_vitals(vitals)
    factors: list[dict[str, Any]] = []
    level = "normal"
    spo2 = normalized.get("spo2")
    systolic = normalized.get("systolic_bp")
    diastolic = normalized.get("diastolic_bp")
    heart_rate = normalized.get("heart_rate")
    temperature = normalized.get("temperature_f")
    if isinstance(spo2, float) and spo2 < VITAL_THRESHOLDS["spo2_critical_lt"]:
        level = "critical"
        factors.append({"label": "SpO2 below emergency threshold", "points": 30, "value": spo2})
    if isinstance(systolic, float) and isinstance(diastolic, float):
        if systolic > VITAL_THRESHOLDS["systolic_critical_gt"] or diastolic > VITAL_THRESHOLDS["diastolic_critical_gt"]:
            level = "critical"
            factors.append({"label": "Blood pressure above emergency threshold", "points": 25, "value": normalized["blood_pressure"]})
    if isinstance(heart_rate, float) and heart_rate > VITAL_THRESHOLDS["heart_rate_urgent_gt"]:
        if level != "critical":
            level = "urgent"
        factors.append({"label": "Heart rate above urgent threshold", "points": 15, "value": heart_rate})
    if isinstance(temperature, float) and temperature > VITAL_THRESHOLDS["temperature_urgent_gt_f"]:
        if level != "critical":
            level = "urgent"
        factors.append({"label": "Temperature above urgent threshold", "points": 15, "value": temperature})
    return {"level": level, "vitals": normalized, "factors": factors}


def questionnaire_risk_factors(questionnaire_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    answers = (questionnaire_payload or {}).get("answers") or {}
    factors: list[dict[str, Any]] = []
    severity = _number(answers.get("pain_severity") or answers.get("severity"))
    if severity is not None and severity >= 8:
        factors.append({"label": "Severe chest pain", "points": 25, "value": severity})
    radiation = str(answers.get("radiation") or "").lower()
    if any(term in radiation for term in {"arm", "jaw", "back", "shoulder", "yes"}):
        factors.append({"label": "Radiating pain pattern", "points": 20, "value": answers.get("radiation")})
    breathing = " ".join(str(answers.get(key) or "") for key in ("breathing_difficulty", "breathing", "chest_pain")).lower()
    if any(term in breathing for term in {"yes", "short", "breath", "difficulty", "trouble"}):
        factors.append({"label": "Breathing difficulty reported", "points": 20, "value": breathing.strip()})
    sweating = str(answers.get("sweating") or "").lower()
    if any(term in sweating for term in {"yes", "sweat", "faint", "weak"}):
        factors.append({"label": "Sweating, faintness, or unusual weakness", "points": 15, "value": answers.get("sweating")})
    duration = str(answers.get("duration") or "").lower()
    duration_numbers = re.findall(r"\d+", duration)
    if duration_numbers and int(duration_numbers[0]) >= 20 and any(unit in duration for unit in {"minute", "min"}):
        factors.append({"label": "Symptoms lasting over 20 minutes", "points": 7, "value": answers.get("duration")})
    if any(term in str(answers.get("visible_deformity") or "").lower() for term in {"yes", "deformed", "out of"}):
        factors.append({"label": "Visible limb deformity", "points": 25, "value": answers.get("visible_deformity")})
    if any(term in str(answers.get("major_trauma") or answers.get("mechanism") or "").lower() for term in {"yes", "accident", "fall", "impact"}):
        factors.append({"label": "Major trauma reported", "points": 20, "value": answers.get("major_trauma") or answers.get("mechanism")})
    if any(term in str(answers.get("severe_bleeding") or answers.get("bleeding") or "").lower() for term in {"yes", "heavy", "severe", "open"}):
        factors.append({"label": "Severe bleeding or open wound", "points": 30, "value": answers.get("severe_bleeding") or answers.get("bleeding")})
    if any(term in str(answers.get("head_injury") or "").lower() for term in {"yes", "loss", "vomit", "unconscious"}):
        factors.append({"label": "Head injury red flag", "points": 30, "value": answers.get("head_injury")})
    if any(term in str(answers.get("sudden_onset") or "").lower() for term in {"yes", "sudden"}):
        factors.append({"label": "Sudden neurological symptom onset", "points": 25, "value": answers.get("sudden_onset")})
    if any(term in str(answers.get("one_sided_weakness") or "").lower() for term in {"yes", "one side", "left", "right"}):
        factors.append({"label": "One-sided weakness or numbness", "points": 30, "value": answers.get("one_sided_weakness")})
    if any(term in str(answers.get("speech_difficulty") or "").lower() for term in {"yes", "slurred", "difficult"}):
        factors.append({"label": "Speech difficulty reported", "points": 25, "value": answers.get("speech_difficulty")})
    if any(term in str(answers.get("facial_droop") or "").lower() for term in {"yes", "droop"}):
        factors.append({"label": "Facial drooping reported", "points": 25, "value": answers.get("facial_droop")})
    return factors


def build_risk_explanation(*, analysis: dict[str, Any], questionnaire_payload: dict[str, Any] | None, vitals_payload: dict[str, Any] | None) -> dict[str, Any]:
    factors: list[dict[str, Any]] = []
    for feature in analysis.get("ml_governance", {}).get("active_prediction", {}).get("top_features", []) or []:
        factors.append({"label": str(feature.get("feature", "")).replace("_", " "), "points": round(float(feature.get("contribution", 0)) * 10, 1), "value": feature.get("value")})
    factors.extend(questionnaire_risk_factors(questionnaire_payload))
    vitals_evaluation = evaluate_vitals(vitals_payload)
    factors.extend(vitals_evaluation["factors"])
    patient_age = analysis.get("known_context", {}).get("used_age")
    if patient_age and int(patient_age) >= 60:
        factors.append({"label": "Age over 60", "points": 10, "value": patient_age})
    score = max(float(analysis.get("priority_score", 0.0) or 0.0), sum(float(item["points"]) for item in factors))
    if vitals_evaluation["level"] == "critical":
        level = "EMERGENCY"
        score = max(score, 95.0)
    elif str(analysis.get("urgency", "")).lower() == "emergency":
        level = "EMERGENCY"
        score = max(score, 90.0)
    elif vitals_evaluation["level"] == "urgent" or str(analysis.get("urgency", "")).lower() == "high":
        level = "URGENT"
        score = max(score, 74.0)
    elif score >= 45:
        level = "MODERATE"
    else:
        level = "LOW"
    return {
        "risk_score": round(min(score, 100.0), 1),
        "risk_level": level,
        "contributing_factors": [str(item["label"]) for item in factors if item.get("label")],
        "factor_breakdown": factors,
        "vitals_evaluation": vitals_evaluation,
    }


def build_clinical_summary(analysis: dict[str, Any]) -> str:
    symptoms = ", ".join(analysis.get("extracted_symptoms") or []) or str(analysis.get("symptoms") or "reported symptoms")
    risk = analysis.get("risk_explanation", {})
    factors = risk.get("contributing_factors") or []
    factor_text = "; ".join(factors[:4]) if factors else "available intake context"
    return (
        f"Patient reports {symptoms}. DOCQ classified workflow risk as {risk.get('risk_level', analysis.get('urgency', 'unknown'))} "
        f"based on {factor_text}. This is a triage summary for clinician review, not a diagnosis."
    )
