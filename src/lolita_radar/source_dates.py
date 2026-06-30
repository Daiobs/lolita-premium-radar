from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


CURRENT_SOURCE_WINDOW_DAYS = 90


def is_current_source_date(value: str) -> bool:
    source_date = parse_source_date(value)
    if source_date is None:
        return False
    today = current_source_date()
    return source_date.year >= current_year() and recent_source_cutoff_date(today) <= source_date <= today


def parse_source_date(value: str) -> date | None:
    raw = str(value or "").strip()
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def current_source_date() -> date:
    return datetime.now(timezone.utc).date()


def recent_source_cutoff_date(today: date | None = None) -> date:
    return (today or current_source_date()) - timedelta(days=CURRENT_SOURCE_WINDOW_DAYS)


def current_year() -> int:
    return datetime.now(timezone.utc).year
