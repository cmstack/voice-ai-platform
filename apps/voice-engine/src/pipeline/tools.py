"""In-call tools registered with Nova Sonic. Handlers close over the call's ToolRuntime.

Every tool goes through the conversation flow (pipeline/flow.py): calls that are out of order for
the current stage are rejected, and every result carries the next step's instructions.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from src import db
from src.config import get_settings
from src.pipeline.flow import END_REASONS, FlowState, Stage, answer_key, parse_future
from src.pipeline.sanitize import clean_name, clean_text
from src.schemas import CallContext
from src.services import calendar, dial_guard, telephony

TRANSFER_LINE = "Hold on just a second while I connect you with a specialist."
TRANSFER_SPEECH_DELAY_SECS = 4.0
HANGUP_DELAY_SECS = 6.0  # time for the goodbye to finish playing
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)


@dataclass
class ToolRuntime:
    call: CallContext
    config: dict[str, Any]
    contact: dict[str, Any]
    flow: FlowState = field(init=False)
    offered_slots: dict[str, datetime] = field(default_factory=dict)
    availability_checks: int = 0
    callbacks_scheduled: int = 0
    booked: bool = False
    transferred: bool = False
    hangup: Callable[[], Awaitable[None]] | None = None
    background: set[asyncio.Task] = field(default_factory=set)

    def __post_init__(self):
        self.flow = FlowState(questions=list(self.config.get("qualification_questions") or []))

    def run_later(self, delay: float, coro_fn: Callable[[], Awaitable[None]]) -> None:
        async def later():
            await asyncio.sleep(delay)
            await coro_fn()

        task = asyncio.create_task(later())
        self.background.add(task)
        task.add_done_callback(self.background.discard)


# --- qualify -----------------------------------------------------------------


async def save_qualification(rt: ToolRuntime, ready_to_book: bool = False, **answers: str) -> dict:
    known = {answer_key(q) for q in rt.flow.questions}
    saved = {k: clean_text(v) for k, v in answers.items() if k in known and clean_text(v)}
    rt.flow.answers.update(saved)
    if ready_to_book:
        rt.flow.enter(Stage.SCHEDULE)
        return {"success": True, "saved": sorted(saved)}
    return {"success": True, "saved": sorted(saved), "still_missing": rt.flow.missing_questions()}


# --- schedule ----------------------------------------------------------------


async def check_calendar_availability(rt: ToolRuntime, date_str: str, timezone: str | None = None) -> dict:
    tz = timezone or rt.config["timezone"]
    try:
        day = date.fromisoformat(date_str[:10])
    except ValueError:
        return {"error": "date_str must be YYYY-MM-DD"}
    if rt.availability_checks >= get_settings().max_availability_checks_per_call:
        return {"error": "Too many calendar lookups on this call. Offer to schedule a callback instead."}
    rt.availability_checks += 1
    if rt.flow.stage == Stage.CALLBACK:  # caller changed their mind and wants to book after all
        rt.flow.callback_at = None
    rt.flow.enter(Stage.SCHEDULE)
    try:
        slots = await calendar.available_slots(rt.config["cal_event_type_id"], day, tz)
        searched = "that day"
        if not slots:
            slots = await calendar.available_slots(rt.config["cal_event_type_id"], day, tz, days_ahead=3)
            searched = "the next few days"
    except calendar.CalendarError as exc:
        logger.warning(f"Calendar lookup failed: {exc}")
        return {"error": "The calendar is unavailable right now. Offer to schedule a callback instead."}
    picks = calendar.pick_spread(slots)
    for s in picks:
        rt.offered_slots[s.start.isoformat()] = s.start
    rt.flow.slots_offered = rt.flow.slots_offered or bool(picks)
    if not picks:
        return {"slots": [], "message": f"No openings on {searched}. Ask for another day."}
    return {
        "searched": searched,
        "slots": [{"slot_iso": s.start.isoformat(), "spoken": s.spoken(tz)} for s in picks],
    }


def _offered(rt: ToolRuntime, slot_iso: str) -> datetime | None:
    try:
        start = datetime.fromisoformat(slot_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return start if any(start == s for s in rt.offered_slots.values()) else None


async def book_calendar_appointment(
    rt: ToolRuntime, slot_iso: str, attendee_name: str, attendee_email: str, notes: str = ""
) -> dict:
    attendee_email = clean_text(attendee_email, 254).replace(" ", "")
    attendee_name = clean_name(attendee_name) or ""
    notes = clean_text(notes, 500) if notes else ""
    if not attendee_name:
        return {"success": False, "error": "Ask the caller for their full name before booking."}
    if not EMAIL.match(attendee_email):
        return {
            "success": False,
            "error": f"'{attendee_email}' is not a complete email address. Ask the caller to spell it letter by letter, "
                     "including the part after the at sign, then try again.",
        }
    recent = await db.count_bookings_since(datetime.now(timezone.utc) - timedelta(hours=1))
    if recent >= get_settings().max_bookings_per_hour:
        logger.warning("Booking refused: hourly booking limit reached")
        return {"success": False, "error": "Booking is unavailable right now. Offer to schedule a callback instead."}
    upcoming = await db.count_upcoming_appointments(rt.call.contact_id)
    if upcoming >= get_settings().max_upcoming_appointments_per_contact:
        return {
            "success": False,
            "error": "This caller already has an upcoming appointment. Don't book another. Offer to transfer them "
                     "to a specialist if they want to change it.",
        }
    start = _offered(rt, slot_iso)
    if start is None:
        return {
            "success": False,
            "error": "Only book a slot_iso that check_calendar_availability returned on this call.",
        }
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
    rt.flow.enter(Stage.BOOKED)
    return {"success": True, "confirmed_time": calendar.Slot(booking.start).spoken(rt.config["timezone"])}


# --- exits ---------------------------------------------------------------------


async def schedule_callback(rt: ToolRuntime, callback_iso: str, reason: str = "") -> dict:
    if rt.callbacks_scheduled >= get_settings().max_callbacks_per_call:
        return {"success": False, "error": "A callback is already set for this call. Confirm it and wrap up."}
    when = parse_future(callback_iso)
    if when is not None and when > datetime.now(when.tzinfo) + timedelta(days=60):
        return {"success": False, "error": "Callbacks can be scheduled at most 60 days ahead. Ask for an earlier time."}
    if when is None:
        return {
            "success": False,
            "error": "callback_iso must be a future ISO 8601 time with a UTC offset, e.g. 2026-10-20T15:00:00-05:00.",
        }
    rt.callbacks_scheduled += 1
    rt.flow.callback_at = when
    rt.flow.callback_reason = clean_text(reason) or None
    rt.flow.enter(Stage.CALLBACK)
    # The caller asked to be called back: record that as consent to dial this number.
    await db.record_consent(rt.call.contact_id, f"callback requested by caller on call {rt.call.call_log_id}")
    return {"success": True, "callback": calendar.Slot(when).spoken(rt.config["timezone"])}


async def transfer_to_human(rt: ToolRuntime, reason: str) -> dict:
    phone = rt.config.get("human_transfer_phone")
    if not phone:
        return {"success": False, "message": "No specialist line is configured. Offer a callback instead."}
    if not dial_guard.country_allowed(phone, get_settings().allowed_dial_country_codes):
        logger.warning("Transfer refused: transfer number is outside the allowed calling countries")
        return {"success": False, "message": "Transfers are unavailable right now. Offer a callback instead."}

    await db.set_stage(rt.call.contact_id, "transferred_to_human")
    rt.transferred = True
    rt.flow.enter(Stage.TRANSFERRED)
    logger.info(f"Transferring call {rt.call.call_log_id} to human: {reason}")

    if rt.call.transport == "twilio" and rt.call.provider_call_id:
        rt.run_later(TRANSFER_SPEECH_DELAY_SECS, lambda: telephony.bridge_call(rt.call.provider_call_id, phone))
        return {"success": True, "say": TRANSFER_LINE}
    return {"success": True, "say": TRANSFER_LINE, "note": "Browser test call: the transfer is simulated."}


async def end_call(rt: ToolRuntime, reason: str = "completed") -> dict:
    rt.flow.end_reason = reason if reason in END_REASONS else "completed"
    rt.flow.enter(Stage.CLOSED)
    if rt.hangup:
        rt.run_later(HANGUP_DELAY_SECS, rt.hangup)
    return {"success": True, "say": "Say a short goodbye now. The line will close after it."}


# --- registration ----------------------------------------------------------------


def run_tool(rt: ToolRuntime, name: str, fn: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
    """Wrap a tool with the stage guard and the `next` instruction. Exposed for tests."""

    async def run(**arguments: Any) -> dict:
        if not rt.flow.allows(name):
            logger.info(f"Flow rejected {name} in stage {rt.flow.stage}")
            return rt.flow.rejection(name)
        try:
            result = await fn(rt, **arguments)
        except TypeError as exc:
            result = {"error": f"bad arguments: {exc}"}
        return {**result, **rt.flow.step()}

    return run


def build_tools(rt: ToolRuntime) -> list[FunctionSchema]:
    def handler(name: str, fn):
        guarded = run_tool(rt, name, fn)

        async def run(params: FunctionCallParams):
            try:
                result = await guarded(**params.arguments)
            except Exception:
                logger.exception(f"Tool {name} crashed")
                result = {"error": "Something went wrong. Apologise briefly and offer a callback.", **rt.flow.step()}
            await params.result_callback(result)

        return run

    qualification_props = {
        answer_key(q): {"type": "string", "description": f"Caller's answer to: {q}"} for q in rt.flow.questions
    }

    def schema(name: str, fn, description: str, properties: dict, required: list[str]) -> FunctionSchema:
        return FunctionSchema(name=name, description=description, properties=properties, required=required,
                              handler=handler(name, fn))

    return [
        schema(
            "save_qualification", save_qualification,
            "Save the caller's answers to the qualification questions. Set ready_to_book when they want to pick a time.",
            {**qualification_props, "ready_to_book": {"type": "boolean", "description": "True when the caller wants to book now"}},
            ["ready_to_book"],
        ),
        schema(
            "check_calendar_availability", check_calendar_availability,
            "Look up open appointment times on a given day. Returns up to three options.",
            {
                "date_str": {"type": "string", "description": "Day to search, YYYY-MM-DD"},
                "timezone": {"type": "string", "description": "IANA timezone of the caller, e.g. America/Chicago"},
            },
            ["date_str"],
        ),
        schema(
            "book_calendar_appointment", book_calendar_appointment,
            "Book one of the offered slots after the caller agreed to it and confirmed name and email.",
            {
                "slot_iso": {"type": "string", "description": "slot_iso value from check_calendar_availability"},
                "attendee_name": {"type": "string"},
                "attendee_email": {"type": "string"},
                "notes": {"type": "string", "description": "Short note on the caller's goals and timeline"},
            },
            ["slot_iso", "attendee_name", "attendee_email"],
        ),
        schema(
            "schedule_callback", schedule_callback,
            "Schedule a call back at a specific time the caller asked for.",
            {
                "callback_iso": {"type": "string", "description": "ISO 8601 time with UTC offset, e.g. 2026-10-20T15:00:00-05:00"},
                "reason": {"type": "string", "description": "Why they want a callback"},
            },
            ["callback_iso"],
        ),
        schema(
            "transfer_to_human", transfer_to_human,
            "Connect the caller to a human specialist.",
            {"reason": {"type": "string", "description": "Why the caller needs a human"}},
            ["reason"],
        ),
        schema(
            "end_call", end_call,
            "End the call after saying goodbye.",
            {"reason": {"type": "string", "enum": list(END_REASONS), "description": "Why the call is ending"}},
            ["reason"],
        ),
    ]
