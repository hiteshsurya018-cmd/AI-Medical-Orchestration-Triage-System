from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


HOSPITAL_TIMEZONE = ZoneInfo("Asia/Kolkata")


def get_current_time() -> datetime:
    return datetime.now(HOSPITAL_TIMEZONE)


def get_current_date() -> date:
    return get_current_time().date()


def is_future_slot(slot_date: str, slot_time: str) -> bool:
    try:
        slot_start = datetime.fromisoformat(f"{slot_date}T{slot_time}").replace(tzinfo=HOSPITAL_TIMEZONE)
    except ValueError:
        return False
    return slot_start > get_current_time()
