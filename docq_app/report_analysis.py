from __future__ import annotations

import re
from pathlib import Path
from typing import BinaryIO

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename


LAB_RULES: dict[str, dict[str, object]] = {
    "hemoglobin": {"aliases": ["hemoglobin", "hb"], "unit": "g/dL", "low": 12.0, "high": 17.5, "report_type": "CBC"},
    "wbc": {"aliases": ["wbc", "white blood cells", "white blood cell count"], "unit": "cells/uL", "low": 4000, "high": 11000, "report_type": "CBC"},
    "platelets": {"aliases": ["platelets", "platelet count"], "unit": "cells/uL", "low": 150000, "high": 450000, "report_type": "CBC"},
    "fasting_glucose": {"aliases": ["fasting glucose", "fasting blood sugar", "fbs"], "unit": "mg/dL", "low": 70, "high": 125, "report_type": "Blood Sugar"},
    "random_glucose": {"aliases": ["random glucose", "random blood sugar", "rbs"], "unit": "mg/dL", "low": 70, "high": 200, "report_type": "Blood Sugar"},
    "hba1c": {"aliases": ["hba1c", "hb a1c"], "unit": "%", "low": 4.0, "high": 6.4, "report_type": "Blood Sugar"},
    "total_cholesterol": {"aliases": ["total cholesterol", "cholesterol"], "unit": "mg/dL", "low": None, "high": 200, "report_type": "Lipid Profile"},
    "ldl": {"aliases": ["ldl", "ldl cholesterol"], "unit": "mg/dL", "low": None, "high": 130, "report_type": "Lipid Profile"},
    "hdl": {"aliases": ["hdl", "hdl cholesterol"], "unit": "mg/dL", "low": 40, "high": None, "report_type": "Lipid Profile"},
    "triglycerides": {"aliases": ["triglycerides", "tg"], "unit": "mg/dL", "low": None, "high": 150, "report_type": "Lipid Profile"},
    "tsh": {"aliases": ["tsh", "thyroid stimulating hormone"], "unit": "mIU/L", "low": 0.4, "high": 4.5, "report_type": "Thyroid"},
    "t3": {"aliases": ["t3"], "unit": "ng/dL", "low": 80, "high": 180, "report_type": "Thyroid"},
    "t4": {"aliases": ["t4"], "unit": "ug/dL", "low": 5.0, "high": 12.0, "report_type": "Thyroid"},
}

ALLOWED_REPORT_EXTENSIONS = {".txt", ".csv", ".pdf"}


def safe_report_filename(filename: str) -> str:
    safe_name = secure_filename(filename or "report.txt")
    return safe_name or "report.txt"


def extract_text_from_upload(file_storage: FileStorage | None = None, *, raw_text: str = "") -> dict[str, object]:
    if raw_text.strip():
        return {"text": raw_text.strip(), "ocr_status": "completed", "extraction_method": "submitted_text"}
    if file_storage is None or not file_storage.filename:
        return {"text": "", "ocr_status": "no_file", "extraction_method": "none"}
    filename = safe_report_filename(file_storage.filename)
    suffix = Path(filename).suffix.lower()
    payload = file_storage.read()
    file_storage.stream.seek(0)
    if suffix not in ALLOWED_REPORT_EXTENSIONS:
        return {"text": "", "ocr_status": "unsupported_file_type", "extraction_method": suffix or "unknown"}
    text = _decode_bytes(payload)
    status = "completed" if text.strip() else "ocr_pending"
    method = "pdf_text_layer" if suffix == ".pdf" else "plain_text"
    return {"text": text.strip(), "ocr_status": status, "extraction_method": method}


def save_report_file(file_storage: FileStorage, upload_dir: Path) -> str:
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_report_filename(file_storage.filename)
    destination = upload_dir / filename
    counter = 1
    while destination.exists():
        destination = upload_dir / f"{destination.stem}-{counter}{destination.suffix}"
        counter += 1
    file_storage.save(destination)
    return destination.name


def analyze_report_text(text: str) -> dict[str, object]:
    normalized = _normalize_text(text)
    lab_values = _extract_lab_values(normalized)
    abnormal_findings = [_evaluate_lab_value(key, item) for key, item in lab_values.items()]
    abnormal_findings = [item for item in abnormal_findings if item is not None]
    report_type = _infer_report_type(lab_values, normalized)
    summary = _build_summary(report_type, lab_values, abnormal_findings)
    return {
        "report_type": report_type,
        "lab_values": lab_values,
        "abnormal_findings": abnormal_findings,
        "summary": summary,
    }


def _decode_bytes(payload: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = ""
    return "".join(char if char.isprintable() or char in "\n\r\t" else " " for char in text)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\x00", " ")).strip()


def _extract_lab_values(text: str) -> dict[str, dict[str, object]]:
    values: dict[str, dict[str, object]] = {}
    for key, rule in LAB_RULES.items():
        for alias in rule["aliases"]:
            escaped = re.escape(str(alias))
            pattern = rf"\b{escaped}\b\s*[:=\-]?\s*([0-9]+(?:\.[0-9]+)?)"
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                values[key] = {
                    "label": _label_from_key(key),
                    "value": float(match.group(1)),
                    "unit": rule["unit"],
                    "source_alias": alias,
                    "reference_low": rule["low"],
                    "reference_high": rule["high"],
                }
                break
    return values


def _evaluate_lab_value(key: str, item: dict[str, object]) -> dict[str, object] | None:
    value = float(item["value"])
    low = item.get("reference_low")
    high = item.get("reference_high")
    if low is not None and value < float(low):
        return {
            "lab": item["label"],
            "value": value,
            "unit": item["unit"],
            "severity": "abnormal",
            "direction": "low",
            "message": f"{item['label']} is below the configured reference range.",
        }
    if high is not None and value > float(high):
        return {
            "lab": item["label"],
            "value": value,
            "unit": item["unit"],
            "severity": "abnormal",
            "direction": "high",
            "message": f"{item['label']} is above the configured reference range.",
        }
    return None


def _infer_report_type(lab_values: dict[str, dict[str, object]], text: str) -> str:
    if lab_values:
        counts: dict[str, int] = {}
        for key in lab_values:
            report_type = str(LAB_RULES[key]["report_type"])
            counts[report_type] = counts.get(report_type, 0) + 1
        return sorted(counts.items(), key=lambda item: item[1], reverse=True)[0][0]
    lowered = text.lower()
    for label in ["CBC", "Blood Sugar", "Lipid Profile", "Thyroid"]:
        if label.lower() in lowered:
            return label
    return "unknown"


def _build_summary(report_type: str, lab_values: dict[str, dict[str, object]], abnormal_findings: list[dict[str, object]]) -> str:
    if not lab_values:
        return "No supported lab values were extracted from this report."
    if not abnormal_findings:
        return f"{report_type} parsed with {len(lab_values)} supported value(s); no configured abnormality flags detected."
    findings = ", ".join(f"{item['lab']} {item['direction']}" for item in abnormal_findings[:4])
    return f"{report_type} parsed with {len(abnormal_findings)} abnormal finding(s): {findings}."


def _label_from_key(key: str) -> str:
    return key.replace("_", " ").title()
