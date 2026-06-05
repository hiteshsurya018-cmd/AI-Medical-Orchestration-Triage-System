from __future__ import annotations

import datetime as dt
import json
from typing import Any

from .constants import DEFAULT_SLOT_TIMES, SPECIALTY_LABELS
from .db import get_connection
from .ml import normalize_specialty
from .time_utils import get_current_date, get_current_time, is_future_slot


ACTIVE_APPOINTMENT_STATUSES = {
    "scheduled",
    "doctor-acknowledged",
    "review",
    "urgent-review",
    "rescheduled",
    "checked-in",
    "follow-up",
    "care-plan-issued",
}
AVAILABLE_DOCTOR_STATES = {"available", "emergency duty"}
UNAVAILABLE_DOCTOR_STATES = {"on leave", "offline", "unavailable", "conference", "busy"}
WEEKDAY_KEYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def availability_band(open_count: int, unavailable: bool = False) -> str:
    if unavailable:
        return "unavailable"
    if open_count <= 0:
        return "booked"
    if open_count <= 4:
        return "low"
    if open_count <= 11:
        return "moderate"
    return "available"


def queue_load_label(daily_count: int) -> str:
    if daily_count >= 14:
        return "High"
    if daily_count >= 7:
        return "Moderate"
    return "Low"


def default_working_hours() -> dict[str, list[str]]:
    return {
        "monday": ["09:00", "17:00"],
        "tuesday": ["09:00", "17:00"],
        "wednesday": ["09:00", "17:00"],
        "thursday": ["09:00", "17:00"],
        "friday": ["09:00", "17:00"],
        "saturday": ["09:00", "13:00"],
    }


def default_schedule_json() -> str:
    return json.dumps(default_working_hours(), sort_keys=True)


def ensure_scheduling_tables() -> None:
    with get_connection() as connection:
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


def sync_default_doctor_schedules(doctors: list[dict[str, object]]) -> None:
    ensure_scheduling_tables()
    now = get_current_time().isoformat(timespec="seconds")
    with get_connection() as connection:
        for doctor in doctors:
            connection.execute(
                """
                INSERT OR IGNORE INTO doctor_schedule_rules (
                    doctor_name, specialty, branch, working_hours_json, slot_interval_minutes, status, updated_at
                ) VALUES (?, ?, ?, ?, 30, 'active', ?)
                """,
                (
                    str(doctor["doctor_name"]),
                    str(doctor.get("specialty") or "General"),
                    str(doctor.get("branch") or "Mysore Central"),
                    default_schedule_json(),
                    now,
                ),
            )


def doctor_is_available_state(raw_state: str | None) -> bool:
    state = str(raw_state or "Available").strip().lower()
    if state in UNAVAILABLE_DOCTOR_STATES:
        return False
    return state in AVAILABLE_DOCTOR_STATES or state == ""


def fetch_doctor_state(doctor_name: str) -> dict[str, str]:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT doctor_name, name, specialty, branch, COALESCE(specialization, '') AS specialization,
                   COALESCE(availability, 'Available') AS availability, COALESCE(status, 'active') AS status
            FROM users
            WHERE doctor_name = ? AND role IN ('doctor', 'clinician')
            LIMIT 1
            """,
            (doctor_name,),
        ).fetchone()
    if row:
        return {
            "doctor_name": str(row["doctor_name"] or doctor_name),
            "display_name": str(row["name"] or doctor_name),
            "specialty": normalize_specialty(str(row["specialty"] or "General")),
            "branch": str(row["branch"] or "Mysore Central"),
            "specialization": str(row["specialization"] or ""),
            "availability": str(row["availability"] or "Available"),
            "status": str(row["status"] or "active"),
        }
    fallback = next((item for item in SPECIALTY_LABELS.values() if str(item.get("doctor")) == doctor_name), None)
    return {
        "doctor_name": doctor_name,
        "display_name": doctor_name,
        "specialty": "General",
        "branch": str(fallback.get("branch") if fallback else "Mysore Central"),
        "specialization": "",
        "availability": "Available",
        "status": "active",
    }


def _booked_times(connection, doctor_name: str, slot_date: str) -> set[str]:
    rows = connection.execute(
        """
        SELECT slot_time
        FROM appointments
        WHERE doctor_name = ?
          AND appointment_date = ?
          AND status NOT IN ('cancelled')
        """,
        (doctor_name, slot_date),
    ).fetchall()
    return {str(row["slot_time"]) for row in rows if row["slot_time"]}


def _unavailable_reason(connection, doctor_name: str, slot_date: str) -> str:
    row = connection.execute(
        """
        SELECT reason
        FROM doctor_unavailability
        WHERE doctor_name = ? AND unavailable_date = ? AND status = 'active'
        ORDER BY id DESC
        LIMIT 1
        """,
        (doctor_name, slot_date),
    ).fetchone()
    return str(row["reason"]) if row else ""


def _slot_rows(connection, doctor_name: str, start_date: str, end_date: str):
    return connection.execute(
        """
        SELECT id, doctor_name, specialty, branch, slot_date, slot_time, status, appointment_id
        FROM doctor_slots
        WHERE doctor_name = ? AND slot_date BETWEEN ? AND ?
        ORDER BY slot_date ASC, slot_time ASC
        """,
        (doctor_name, start_date, end_date),
    ).fetchall()


def build_doctor_calendar(
    doctor_name: str,
    *,
    start_date: str | None = None,
    days: int = 14,
    preferred_date: str = "",
    specialty: str = "",
) -> dict[str, object]:
    ensure_scheduling_tables()
    today = get_current_date()
    requested_start = dt.date.fromisoformat(start_date) if start_date else today
    if requested_start < today:
        requested_start = today
    end_date = requested_start + dt.timedelta(days=max(1, days) - 1)
    doctor_state = fetch_doctor_state(doctor_name)
    doctor_available = doctor_state["status"] == "active" and doctor_is_available_state(doctor_state["availability"])
    specialty = normalize_specialty(specialty or doctor_state["specialty"])

    with get_connection() as connection:
        slot_rows = _slot_rows(connection, doctor_name, requested_start.isoformat(), end_date.isoformat())
        slots_by_date: dict[str, list[dict[str, object]]] = {}
        for row in slot_rows:
            slot_date = str(row["slot_date"])
            slot_time = str(row["slot_time"])
            raw_status = str(row["status"] or "available")
            open_now = raw_status == "available" and doctor_available and is_future_slot(slot_date, slot_time)
            slot_status = "available" if open_now else raw_status
            if raw_status == "available" and not open_now:
                slot_status = "expired" if slot_date == today.isoformat() else "unavailable"
            slots_by_date.setdefault(slot_date, []).append(
                {
                    "id": int(row["id"]),
                    "time": slot_time,
                    "status": slot_status,
                    "available": open_now,
                    "label": "Available" if open_now else ("Booked" if raw_status == "booked" else "Unavailable"),
                    "doctor_name": doctor_name,
                    "department": specialty,
                    "branch": str(row["branch"] or doctor_state["branch"]),
                    "room": f"{str(row['branch'] or doctor_state['branch']).split()[0]}-{slot_time.replace(':', '')}",
                }
            )

        days_payload: list[dict[str, object]] = []
        first_available: dict[str, object] | None = None
        for offset in range(max(1, days)):
            date_obj = requested_start + dt.timedelta(days=offset)
            date_key = date_obj.isoformat()
            reason = _unavailable_reason(connection, doctor_name, date_key)
            day_slots = slots_by_date.get(date_key, [])
            if reason or not doctor_available:
                for slot in day_slots:
                    if slot["available"]:
                        slot["available"] = False
                        slot["status"] = "unavailable"
                        slot["label"] = reason or doctor_state["availability"]
            open_slots = [slot for slot in day_slots if slot["available"]]
            booked_count = sum(1 for slot in day_slots if slot["status"] == "booked")
            if open_slots and first_available is None:
                first_available = {"date": date_key, "time": open_slots[0]["time"], "doctor_name": doctor_name}
            band = availability_band(len(open_slots), unavailable=bool(reason) or not doctor_available or not day_slots)
            daily_load = booked_count
            days_payload.append(
                {
                    "date": date_key,
                    "weekday": WEEKDAY_KEYS[date_obj.weekday()],
                    "is_today": date_obj == today,
                    "is_past": date_obj < today,
                    "preferred": preferred_date == date_key,
                    "recommended": False,
                    "availability": band,
                    "available_slots": len(open_slots),
                    "booked_slots": booked_count,
                    "total_slots": len(day_slots),
                    "doctors_available": 1 if open_slots else 0,
                    "earliest_slot": open_slots[0]["time"] if open_slots else "",
                    "queue_load": queue_load_label(daily_load),
                    "unavailable_reason": reason or (doctor_state["availability"] if not doctor_available else ""),
                    "slots": day_slots,
                }
            )
    if first_available:
        for day in days_payload:
            if day["date"] == first_available["date"]:
                day["recommended"] = True
                break
    return {
        "doctor": doctor_state,
        "specialty": specialty,
        "start_date": requested_start.isoformat(),
        "days": days_payload,
        "first_available": first_available,
        "generated_at": get_current_time().isoformat(timespec="seconds"),
    }


def build_department_calendar(
    specialty: str,
    *,
    doctors: list[str],
    start_date: str | None = None,
    days: int = 14,
    preferred_date: str = "",
) -> dict[str, object]:
    today = get_current_date()
    requested_start = dt.date.fromisoformat(start_date) if start_date else today
    if requested_start < today:
        requested_start = today
    doctor_calendars = [
        build_doctor_calendar(doctor, start_date=requested_start.isoformat(), days=days, preferred_date=preferred_date, specialty=specialty)
        for doctor in doctors
    ]
    days_payload: list[dict[str, object]] = []
    recommendation: dict[str, object] | None = None
    for offset in range(max(1, days)):
        date_key = (requested_start + dt.timedelta(days=offset)).isoformat()
        day_entries = [calendar["days"][offset] for calendar in doctor_calendars if len(calendar["days"]) > offset]
        available_slots = sum(int(day["available_slots"]) for day in day_entries)
        booked_slots = sum(int(day["booked_slots"]) for day in day_entries)
        doctors_available = sum(1 for day in day_entries if int(day["available_slots"]) > 0)
        earliest_candidates = [str(day["earliest_slot"]) for day in day_entries if day["earliest_slot"]]
        earliest_slot = min(earliest_candidates) if earliest_candidates else ""
        band = availability_band(available_slots, unavailable=not day_entries or doctors_available == 0)
        days_payload.append(
            {
                "date": date_key,
                "is_today": date_key == today.isoformat(),
                "preferred": preferred_date == date_key,
                "recommended": False,
                "availability": band,
                "available_slots": available_slots,
                "booked_slots": booked_slots,
                "doctors_available": doctors_available,
                "earliest_slot": earliest_slot,
                "queue_load": queue_load_label(booked_slots),
            }
        )
        if recommendation is None and earliest_slot:
            recommendation = {"date": date_key, "time": earliest_slot}
    if recommendation:
        for day in days_payload:
            if day["date"] == recommendation["date"]:
                day["recommended"] = True
                break
    return {
        "specialty": normalize_specialty(specialty),
        "days": days_payload,
        "recommendation": recommendation,
        "generated_at": get_current_time().isoformat(timespec="seconds"),
    }


def doctor_workload(doctor_name: str, *, reference_date: str | None = None) -> dict[str, object]:
    today = reference_date or get_current_date().isoformat()
    week_start = dt.date.fromisoformat(today)
    week_end = week_start + dt.timedelta(days=6)
    with get_connection() as connection:
        daily = int(
            connection.execute(
                "SELECT COUNT(*) FROM appointments WHERE doctor_name = ? AND appointment_date = ? AND status NOT IN ('cancelled')",
                (doctor_name, today),
            ).fetchone()[0]
        )
        weekly = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM appointments
                WHERE doctor_name = ? AND appointment_date BETWEEN ? AND ? AND status NOT IN ('cancelled')
                """,
                (doctor_name, week_start.isoformat(), week_end.isoformat()),
            ).fetchone()[0]
        )
        followups = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM appointments
                WHERE doctor_name = ? AND follow_up_status NOT IN ('', 'none') AND status NOT IN ('cancelled')
                """,
                (doctor_name,),
            ).fetchone()[0]
        )
        emergency = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM appointments
                WHERE doctor_name = ? AND urgency = 'Emergency' AND status NOT IN ('cancelled')
                """,
                (doctor_name,),
            ).fetchone()[0]
        )
    return {
        "patients_today": daily,
        "weekly_load": weekly,
        "pending_followups": followups,
        "emergency_queue": emergency,
        "queue_load": queue_load_label(daily),
        "availability_score": max(0, min(100, 100 - (daily * 5) - (emergency * 8) - (followups * 2))),
    }


def rank_doctor_availability(candidates: list[dict[str, object]], *, preferred_date: str = "") -> list[dict[str, object]]:
    ranked: list[dict[str, object]] = []
    for candidate in candidates:
        doctor_name = str(candidate["doctor_name"])
        calendar = build_doctor_calendar(doctor_name, start_date=preferred_date or None, days=7, preferred_date=preferred_date, specialty=str(candidate.get("specialty") or ""))
        workload = doctor_workload(doctor_name)
        first_available = calendar["first_available"] or {}
        open_count = sum(int(day["available_slots"]) for day in calendar["days"])
        preferred_day = next((day for day in calendar["days"] if day["date"] == preferred_date), None)
        preferred_bonus = 20 if preferred_day and int(preferred_day["available_slots"]) > 0 else 0
        score = float(candidate.get("score", 60)) + preferred_bonus + (open_count * 1.5) + (float(workload["availability_score"]) * 0.35)
        ranked.append(
            {
                **candidate,
                "score": round(score, 1),
                "availability_score": workload["availability_score"],
                "patients_today": workload["patients_today"],
                "weekly_load": workload["weekly_load"],
                "pending_followups": workload["pending_followups"],
                "emergency_queue": workload["emergency_queue"],
                "queue_load": workload["queue_load"],
                "calendar": calendar,
                "next_available_slot": (
                    f"{first_available.get('date')} {first_available.get('time')}" if first_available else "No live slot available"
                ),
                "open_slot_count": open_count,
                "availability_status": calendar["doctor"]["availability"],
            }
        )
    ranked.sort(key=lambda item: (float(item["score"]), int(item["open_slot_count"]), -int(item["patients_today"])), reverse=True)
    return ranked


def compact_available_dates(calendar: dict[str, object], limit: int = 7) -> list[dict[str, str]]:
    dates = []
    for day in calendar.get("days", []):
        if int(day.get("available_slots", 0)) <= 0:
            continue
        dates.append(
            {
                "date": str(day["date"]),
                "first_time": str(day["earliest_slot"]),
                "open_count": str(day["available_slots"]),
                "availability": str(day["availability"]),
            }
        )
        if len(dates) >= limit:
            break
    return dates


def reserve_best_slot(connection, doctor_name: str, requested_date: str, requested_time: str = ""):
    today = get_current_date().isoformat()
    if requested_date < today:
        requested_date = today
    params: list[Any] = [doctor_name, requested_date]
    time_filter = ""
    if requested_time:
        time_filter = "AND slot_time = ?"
        params.append(requested_time)
    params.extend([requested_date, requested_time or "", requested_time or ""])
    slot = connection.execute(
        f"""
        SELECT *
        FROM doctor_slots
        WHERE doctor_name = ?
          AND slot_date >= ?
          AND status = 'available'
          {time_filter}
        ORDER BY CASE WHEN slot_date = ? THEN 0 ELSE 1 END,
                 CASE WHEN ? != '' AND slot_time = ? THEN 0 ELSE 1 END,
                 slot_date ASC, slot_time ASC
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    if not slot:
        return None
    if not is_future_slot(str(slot["slot_date"]), str(slot["slot_time"])):
        return None
    doctor_state = fetch_doctor_state(str(slot["doctor_name"]))
    if doctor_state["status"] != "active" or not doctor_is_available_state(doctor_state["availability"]):
        return None
    if _unavailable_reason(connection, str(slot["doctor_name"]), str(slot["slot_date"])):
        return None
    if str(slot["slot_time"]) in _booked_times(connection, str(slot["doctor_name"]), str(slot["slot_date"])):
        return None
    cursor = connection.execute(
        "UPDATE doctor_slots SET status = 'reserved' WHERE id = ? AND status = 'available'",
        (slot["id"],),
    )
    if cursor.rowcount != 1:
        return None
    return slot
