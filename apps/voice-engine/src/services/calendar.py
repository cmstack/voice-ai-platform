"""Cal.com API v2 client: availability lookup and booking creation."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from src.config import get_settings

SLOTS_API_VERSION = "2024-09-04"
BOOKINGS_API_VERSION = "2024-08-13"


class CalendarError(RuntimeError):
    def __init__(self, message: str, *, slot_unavailable: bool = False):
        super().__init__(message)
        self.slot_unavailable = slot_unavailable


# Cal.com error text that means the time itself can't be booked (vs. a config or system problem).
_SLOT_GONE = ("no_available_users_found", "not available", "already has booking", "booking limit", "past")


@dataclass
class Slot:
    start: datetime

    def spoken(self, tz: str) -> str:
        local = self.start.astimezone(ZoneInfo(tz))
        return local.strftime("%A, %B %-d at %-I:%M %p").replace(":00 ", " ")


@dataclass
class Booking:
    uid: str
    start: datetime


def _client(timeout: float = 8.0) -> httpx.AsyncClient:
    s = get_settings()
    return httpx.AsyncClient(
        base_url=s.cal_api_base_url,
        headers={"Authorization": f"Bearer {s.cal_api_key}"},
        timeout=httpx.Timeout(timeout),
    )


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def available_slots(
    event_type_id: int, day: date, timezone: str, days_ahead: int = 0
) -> list[Slot]:
    try:
        async with _client() as client:
            resp = await client.get(
                "/slots",
                params={
                    "eventTypeId": event_type_id,
                    "start": day.isoformat(),
                    "end": (day + timedelta(days=days_ahead)).isoformat(),
                    "timeZone": timezone,
                },
                headers={"cal-api-version": SLOTS_API_VERSION},
            )
    except httpx.HTTPError as exc:
        raise CalendarError(f"Cal.com slots request failed: {type(exc).__name__}: {exc}") from exc
    if resp.status_code != 200:
        raise CalendarError(f"Cal.com slots {resp.status_code}: {resp.text[:200]}")
    data = resp.json().get("data", {})
    slots = [Slot(_parse_iso(s["start"])) for day_slots in data.values() for s in day_slots]
    now = datetime.now(ZoneInfo(timezone))
    return sorted((s for s in slots if s.start > now), key=lambda s: s.start)


def pick_spread(slots: list[Slot], n: int = 3) -> list[Slot]:
    """Offer a handful of options spread across the window instead of three back-to-back slots."""
    if len(slots) <= n:
        return slots
    step = (len(slots) - 1) / (n - 1)
    return [slots[round(i * step)] for i in range(n)]


async def create_booking(
    *,
    event_type_id: int,
    start: datetime,
    attendee_name: str,
    attendee_email: str,
    attendee_phone: str | None,
    timezone: str,
    notes: str | None,
) -> Booking:
    attendee = {"name": attendee_name, "email": attendee_email, "timeZone": timezone}
    if attendee_phone:
        attendee["phoneNumber"] = attendee_phone
    payload = {
        "start": start.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "eventTypeId": event_type_id,
        "attendee": attendee,
        "metadata": {"source": "voice-agent"},
    }
    if notes:
        payload["bookingFieldsResponses"] = {"notes": notes}
    # Creating the booking also creates the calendar event and video link, which can take a while.
    try:
        async with _client(timeout=20.0) as client:
            resp = await client.post(
                "/bookings", json=payload, headers={"cal-api-version": BOOKINGS_API_VERSION}
            )
    except httpx.HTTPError as exc:
        raise CalendarError(f"Cal.com booking request failed: {type(exc).__name__}: {exc}") from exc
    if resp.status_code != 201:
        text = resp.text[:300]
        raise CalendarError(
            f"Cal.com booking {resp.status_code}: {text}",
            slot_unavailable=any(marker in text.lower() for marker in _SLOT_GONE),
        )
    data = resp.json()["data"]
    return Booking(uid=data["uid"], start=_parse_iso(data.get("start", payload["start"])))
