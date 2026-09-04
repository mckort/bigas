"""Chat clocks — LLM message prefixes and the system-prompt current time."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

CHAT_TZ = ZoneInfo("Europe/Stockholm")

_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def parse_chat_datetime(value: Any) -> Optional[datetime]:
    """Parse a stored chat timestamp into an aware datetime, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    to_datetime = getattr(value, "to_datetime", None)
    if callable(to_datetime):
        try:
            return parse_chat_datetime(to_datetime())
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_chat_timestamp(value: Any) -> str:
    """Return `[Friday, Sep 4, 2026, 10:45 AM CEST]` or empty if unparseable."""
    dt = parse_chat_datetime(value)
    if dt is None:
        return ""
    local = dt.astimezone(CHAT_TZ)
    hour12 = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    tzname = local.tzname() or "CEST"
    return (
        f"[{_WEEKDAYS[local.weekday()]}, {_MONTHS[local.month - 1]} {local.day}, "
        f"{local.year}, {hour12}:{local.minute:02d} {ampm} {tzname}]"
    )


def prefix_llm_message(text: str, created_at: Any) -> str:
    """Prepend a timestamp to LLM-bound text. Does not change stored content."""
    stamp = format_chat_timestamp(created_at)
    body = text or ""
    if not stamp:
        return body
    if not body:
        return stamp
    return f"{stamp}\n{body}"


def current_time_prompt_block(now: Optional[datetime] = None) -> str:
    """System-prompt clock so the model can resolve today / this week."""
    stamp = format_chat_timestamp(now or datetime.now(timezone.utc)).strip("[]")
    return (
        f"Current time: {stamp}.\n"
        "Use this clock for today, this week, and relative dates. "
        "Timestamps on conversation messages are when those messages were sent."
    )
