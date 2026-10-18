"""Compiles the Nova Sonic system instruction and first-turn seed from agent_config + contact."""

import re
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from src.pipeline.flow import overview

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render_template(template: str, values: dict[str, Any]) -> str:
    def sub(m: re.Match) -> str:
        value = values.get(m.group(1))
        return str(value) if value not in (None, "") else "there" if m.group(1) == "full_name" else ""

    return re.sub(r"\s+([,.?!])", r"\1", _PLACEHOLDER.sub(sub, template)).strip()


def upcoming_dates(now: datetime, days: int = 7) -> str:
    """Weekday-to-date lookup so "Tuesday" resolves to the right YYYY-MM-DD."""
    return ", ".join(f"{d:%A} {d:%Y-%m-%d}" for d in (now + timedelta(days=i) for i in range(1, days + 1)))


def _first_name(full_name: str | None) -> str | None:
    return full_name.split()[0] if full_name else None


def compile_system_instruction(
    *,
    config: dict[str, Any],
    contact: dict[str, Any],
    direction: Literal["inbound", "outbound"],
    upcoming_appointments: list[dict[str, Any]],
    now: datetime | None = None,
) -> str:
    tz = config["timezone"]
    now = (now or datetime.now(ZoneInfo(tz))).astimezone(ZoneInfo(tz))
    values = {**config, "full_name": _first_name(contact.get("full_name"))}
    greeting = render_template(
        config["inbound_greeting" if direction == "inbound" else "outbound_greeting"], values
    )
    objections = "\n".join(
        f"- If the caller says it's {k.replace('_', ' ')}: {v}" for k, v in config["objection_handlers"].items()
    )
    known = {
        "name": contact.get("full_name"),
        "email": contact.get("email"),
        "phone": contact.get("phone"),
        **(contact.get("custom_fields") or {}),
    }
    known_lines = "\n".join(f"- {k}: {v}" for k, v in known.items() if v)

    if direction == "inbound":
        situation = (
            f"You are answering an inbound phone call for {config['company_name']}. "
            f"Open with exactly this greeting: \"{greeting}\""
        )
    else:
        situation = (
            f"You are placing an outbound speed-to-lead call for {config['company_name']} to someone who "
            f"just requested information. Once they answer, open with: \"{greeting}\""
        )

    appointment_note = ""
    if upcoming_appointments:
        when = upcoming_appointments[0]["scheduled_at"].astimezone(ZoneInfo(tz))
        appointment_note = (
            f"\nNote: Caller already has a confirmed booking on {when:%A, %B %-d at %-I:%M %p}. "
            "Ask if they need to reschedule or have a question.\n"
        )

    return f"""You are {config['agent_name']}, a friendly, concise phone agent for {config['company_name']}.
{situation}
{appointment_note}
Today is {now:%A, %B %-d, %Y} ({now:%Y-%m-%d}); the current local time is {now:%-I:%M %p} ({tz}, UTC offset {now.isoformat()[-6:]}).
Upcoming dates: {upcoming_dates(now)}.

What we already know about this person:
{known_lines or '- nothing yet'}

Your goal: qualify the lead and book a short intro appointment on the calendar.

{overview(config["qualification_questions"])}

Objection handling:
{objections}

Rules:
- This is a voice call. Keep every reply to one or two short sentences. Never use lists, markdown, or emojis.
- Before check_calendar_availability or book_calendar_appointment, first say a brief filler such as "Let me check the calendar real quick." so there is no dead air. Call the other tools silently.
- Pass date_str as YYYY-MM-DD and callback times as ISO 8601 with the caller's UTC offset.
- Before booking, confirm the caller's full name and spell their email back to them.
- If you reach a voicemail greeting or answering machine, leave a one-sentence message asking them to call back, then call end_call with reason voicemail.
- Never invent prices, availability, or policies."""


def first_turn_seed(direction: Literal["inbound", "outbound"]) -> str:
    """User-role cue that makes Nova Sonic speak first; it is not shown in the stored transcript."""
    if direction == "inbound":
        return "(The phone call has just connected. Greet the caller now.)"
    return "(The person you called has just picked up. Deliver your opening line now.)"
