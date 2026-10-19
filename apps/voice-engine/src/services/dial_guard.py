"""Outbound dialing policy: every real dial goes through `check()` (called by telephony).

Protects against toll fraud (premium/international numbers), harassment (unbounded redials) and
calling without consent or outside calling hours. Blocks that clear on their own (hours, rate) carry
`retry_at`; permanent ones (country, consent) do not.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src import db
from src.config import get_settings


@dataclass
class DialBlocked(Exception):
    reason: str
    retry_at: datetime | None = None

    def __str__(self) -> str:
        return self.reason


def country_allowed(phone: str, allowed: list[str]) -> bool:
    digits = phone.lstrip("+")
    return any(digits.startswith(code) for code in allowed)


def within_business_hours(config: dict[str, Any], now: datetime | None = None) -> bool:
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(config["timezone"]))
    if local.weekday() >= 5:  # weekends off
        return False
    return config["business_hours_start"] <= local.hour < config["business_hours_end"]


def next_business_open(config: dict[str, Any], now: datetime | None = None) -> datetime:
    tz = ZoneInfo(config["timezone"])
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    for days in range(0, 8):
        day = (local + timedelta(days=days)).date()
        opening = datetime(day.year, day.month, day.day, config["business_hours_start"], tzinfo=tz)
        if opening.weekday() < 5 and opening > local:
            return opening.astimezone(timezone.utc)
    return (local + timedelta(days=1)).astimezone(timezone.utc)


async def check(contact: dict[str, Any], config: dict[str, Any], now: datetime | None = None) -> None:
    """Raise DialBlocked unless this contact may be dialed right now."""
    s = get_settings()
    now = now or datetime.now(timezone.utc)

    if not country_allowed(contact["phone"], s.allowed_dial_country_codes):
        raise DialBlocked(f"{contact['phone']} is outside the allowed calling countries")
    if s.require_dial_consent and contact.get("consent_at") is None:
        raise DialBlocked("no call consent on record for this contact")
    if not within_business_hours(config, now):
        raise DialBlocked("outside calling hours", retry_at=next_business_open(config, now))

    per_number = await db.count_dials(since=now - timedelta(hours=24), contact_id=contact["id"])
    if per_number >= s.max_dials_per_number_per_day:
        raise DialBlocked(
            f"already dialed {per_number} times in 24 hours", retry_at=now + timedelta(hours=24)
        )
    total = await db.count_dials(since=now - timedelta(hours=1))
    if total >= s.max_dials_per_hour:
        raise DialBlocked("hourly dialing limit reached", retry_at=now + timedelta(minutes=15))
