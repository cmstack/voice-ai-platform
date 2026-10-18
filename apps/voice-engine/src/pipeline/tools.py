"""In-call tools registered with Nova Sonic. Handlers close over the call's ToolRuntime."""

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from src import db
from src.schemas import CallContext
from src.services import calendar, telephony

TRANSFER_LINE = "Hold on just a second while I connect you with a specialist."
TRANSFER_SPEECH_DELAY_SECS = 4.0


@dataclass
class ToolRuntime:
    call: CallContext
    config: dict[str, Any]
    contact: dict[str, Any]
    offered_slots: dict[str, datetime] = field(default_factory=dict)
    booked: bool = False
    transferred: bool = False
    background: set[asyncio.Task] = field(default_factory=set)


async def check_calendar_availability(rt: ToolRuntime, date_str: str, timezone: str | None = None) -> dict:
    tz = timezone or rt.config["timezone"]
    try:
        day = date.fromisoformat(date_str[:10])
    except ValueError:
        return {"error": "date_str must be YYYY-MM-DD"}
    try:
        slots = await calendar.available_slots(rt.config["cal_event_type_id"], day, tz)
        searched = "that day"
        if not slots:
            slots = await calendar.available_slots(rt.config["cal_event_type_id"], day, tz, days_ahead=3)
            searched = "the next few days"
    except calendar.CalendarError as exc:
        logger.warning(f"Calendar lookup failed: {exc}")
        return {"error": "The calendar is unavailable right now. Offer to have someone call them back."}
    picks = calendar.pick_spread(slots)
    for s in picks:
        rt.offered_slots[s.start.isoformat()] = s.start
    if not picks:
        return {"slots": [], "message": f"No openings on {searched}. Ask for another day."}
    return {
        "searched": searched,
        "slots": [{"slot_iso": s.start.isoformat(), "spoken": s.spoken(tz)} for s in picks],
    }


async def book_calendar_appointment(
    rt: ToolRuntime, slot_iso: str, attendee_name: str, attendee_email: str, notes: str = ""
) -> dict:
    try:
        start = datetime.fromisoformat(slot_iso.replace("Z", "+00:00"))
    except ValueError:
        return {"success": False, "error": "slot_iso must be one of the slot_iso values returned by check_calendar_availability"}
    try:
        booking = await calendar.create_booking(
            event_type_id=rt.config["cal_event_type_id"],
            start=start,
            attendee_name=attendee_name,
            attendee_email=attendee_email,
            attendee_phone=rt.contact.get("phone"),
            timezone=rt.config["timezone"],
            notes=notes or None,
        )
    except calendar.CalendarError as exc:
        logger.warning(f"Booking failed: {exc}")
        return {"success": False, "error": "That time could not be booked. Offer the caller a different slot."}

    await db.record_booking(
        contact_id=rt.call.contact_id,
        call_log_id=rt.call.call_log_id,
        cal_booking_uid=booking.uid,
        scheduled_at=booking.start,
        notes=notes or None,
        attendee_name=attendee_name,
        attendee_email=attendee_email,
    )
    rt.booked = True
    return {
        "success": True,
        "confirmed_time": calendar.Slot(booking.start).spoken(rt.config["timezone"]),
        "message": "Booked. Confirm the time and let them know a calendar invite is on its way to their email.",
    }


async def transfer_to_human(rt: ToolRuntime, reason: str) -> dict:
    phone = rt.config.get("human_transfer_phone")
    if not phone:
        return {"success": False, "message": "No specialist line is configured. Offer a callback instead."}

    await db.set_stage(rt.call.contact_id, "transferred_to_human")
    rt.transferred = True
    logger.info(f"Transferring call {rt.call.call_log_id} to human: {reason}")

    if rt.call.transport == "twilio" and rt.call.provider_call_id:
        async def bridge():
            await asyncio.sleep(TRANSFER_SPEECH_DELAY_SECS)  # let the hand-off line finish playing
            await telephony.bridge_call(rt.call.provider_call_id, phone)

        task = asyncio.create_task(bridge())
        rt.background.add(task)
        task.add_done_callback(rt.background.discard)
        return {"success": True, "say": TRANSFER_LINE}
    return {"success": True, "say": TRANSFER_LINE, "note": "Browser test call: the transfer is simulated."}


def build_tools(rt: ToolRuntime) -> list[FunctionSchema]:
    def handler(fn):
        async def run(params: FunctionCallParams):
            try:
                result = await fn(rt, **params.arguments)
            except TypeError as exc:
                result = {"error": f"bad arguments: {exc}"}
            except Exception:
                logger.exception(f"Tool {params.function_name} crashed")
                result = {"error": "Something went wrong. Apologise briefly and offer a callback."}
            await params.result_callback(result)

        return run

    return [
        FunctionSchema(
            name="check_calendar_availability",
            description="Look up open appointment times on a given day. Returns up to three options.",
            properties={
                "date_str": {"type": "string", "description": "Day to search, YYYY-MM-DD"},
                "timezone": {"type": "string", "description": "IANA timezone of the caller, e.g. America/Chicago"},
            },
            required=["date_str"],
            handler=handler(check_calendar_availability),
        ),
        FunctionSchema(
            name="book_calendar_appointment",
            description="Book one of the offered slots after the caller agreed to it and confirmed name and email.",
            properties={
                "slot_iso": {"type": "string", "description": "slot_iso value from check_calendar_availability"},
                "attendee_name": {"type": "string"},
                "attendee_email": {"type": "string"},
                "notes": {"type": "string", "description": "Short note on the caller's goals and timeline"},
            },
            required=["slot_iso", "attendee_name", "attendee_email"],
            handler=handler(book_calendar_appointment),
        ),
        FunctionSchema(
            name="transfer_to_human",
            description="Connect the caller to a human specialist.",
            properties={"reason": {"type": "string", "description": "Why the caller needs a human"}},
            required=["reason"],
            handler=handler(transfer_to_human),
        ),
    ]
