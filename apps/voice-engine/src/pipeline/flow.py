"""Flows-style conversation state machine for a single Nova Sonic session.

Nova Sonic fixes its system prompt and tool list when the session opens, so instead of swapping
prompts per node (Pipecat Flows), every tool stays registered and this module:

  * tracks the current stage and the path taken,
  * rejects tool calls that are not allowed in the current stage, and
  * returns the next node's instructions in every tool result (`next`), which Nova reads mid-call.

    qualify ──save_qualification(ready)──▶ schedule ──book──▶ booked
       │  ╲                                   │
       │   ╲──check_calendar_availability─────┘
       ├──schedule_callback──▶ callback ──(caller changes mind: check availability)──▶ schedule
       ├──transfer_to_human──▶ transferred
       └──end_call──▶ closed            (end_call and transfer are allowed from any live stage)
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class Stage(StrEnum):
    QUALIFY = "qualify"
    SCHEDULE = "schedule"
    BOOKED = "booked"
    CALLBACK = "callback"
    TRANSFERRED = "transferred"
    CLOSED = "closed"


TERMINAL = {Stage.TRANSFERRED, Stage.CLOSED}

ALLOWED_TOOLS: dict[Stage, set[str]] = {
    Stage.QUALIFY: {"save_qualification", "check_calendar_availability", "schedule_callback", "transfer_to_human", "end_call"},
    Stage.SCHEDULE: {
        "check_calendar_availability", "book_calendar_appointment", "save_qualification",
        "schedule_callback", "transfer_to_human", "end_call",
    },
    Stage.BOOKED: {"transfer_to_human", "end_call"},
    Stage.CALLBACK: {"check_calendar_availability", "transfer_to_human", "end_call"},
    Stage.TRANSFERRED: set(),
    Stage.CLOSED: set(),
}

END_REASONS = ("completed", "not_interested", "wrong_number", "voicemail")

_STOPWORDS = {
    "what", "whats", "is", "your", "are", "you", "the", "a", "an", "with", "do", "does", "how", "which",
    "working", "of", "for", "to", "in", "on", "we", "can", "i", "my", "our", "be", "have", "any",
}


def answer_key(question: str) -> str:
    """Short snake_case key for a qualification question ("What is your primary goal?" -> primary_goal)."""
    words = [w for w in re.findall(r"[a-z0-9]+", question.lower()) if w not in _STOPWORDS]
    return "_".join(words[:3]) or "answer"


@dataclass
class FlowState:
    questions: list[str]
    stage: Stage = Stage.QUALIFY
    path: list[dict[str, str]] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)
    callback_at: datetime | None = None
    callback_reason: str | None = None
    end_reason: str | None = None
    slots_offered: bool = False
    tool_errors: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self):
        if not self.path:
            self.path.append({"stage": self.stage.value, "at": _now()})

    # --- guards & transitions ---------------------------------------------

    def allows(self, tool: str) -> bool:
        return tool in ALLOWED_TOOLS[self.stage]

    def rejection(self, tool: str) -> dict[str, Any]:
        """Tool result for an out-of-order call: tells the model what it should be doing instead."""
        return {
            "success": False,
            "error": f"{tool} is not available in the {self.stage.value} step.",
            **self.step(),
        }

    def record_error(self, tool: str, reason: str) -> None:
        """Keeps why a tool failed with the call, so a failed booking can be diagnosed after the fact."""
        self.tool_errors.append({"tool": tool, "reason": reason[:300], "at": _now()})

    def enter(self, stage: Stage) -> None:
        if stage != self.stage:
            self.stage = stage
            self.path.append({"stage": stage.value, "at": _now()})

    def step(self) -> dict[str, str]:
        """The `stage` / `next` pair appended to every tool result."""
        return {"stage": self.stage.value, "next": self.instructions()}

    # --- node instructions -------------------------------------------------

    def missing_questions(self) -> list[str]:
        return [q for q in self.questions if answer_key(q) not in self.answers]

    def instructions(self) -> str:
        match self.stage:
            case Stage.QUALIFY:
                missing = self.missing_questions()
                if missing:
                    return (
                        f"Ask the next qualification question naturally: \"{missing[0]}\". "
                        "When you have answers, call save_qualification. If they want to book right away, "
                        "call check_calendar_availability."
                    )
                return "Qualification is complete. Ask which day suits them, then call check_calendar_availability."
            case Stage.SCHEDULE:
                if not self.slots_offered:
                    return (
                        "Ask which day works best for them. Once they name a day, call check_calendar_availability "
                        "for that date. Do not pick a day for them."
                    )
                return (
                    "Offer the times returned by check_calendar_availability, at most three. Once they pick one, "
                    "confirm their full name and spell their email back, then call book_calendar_appointment "
                    "with that exact slot_iso. If none suit them, ask for another day."
                )
            case Stage.BOOKED:
                return (
                    "The appointment is booked. Confirm the time once, say an invite is on its way, ask if there "
                    "is anything else, then say goodbye and call end_call with reason completed."
                )
            case Stage.CALLBACK:
                when = self.callback_at.isoformat() if self.callback_at else "the agreed time"
                return (
                    f"A callback is scheduled for {when}. Confirm the day and time out loud, thank them, "
                    "say goodbye and call end_call with reason completed."
                )
            case Stage.TRANSFERRED:
                return "The call is being transferred. Say nothing more."
            case Stage.CLOSED:
                return "The call is over. Say nothing more."

    def summary(self) -> dict[str, Any]:
        """Stored on call_logs.extracted_data.flow for the dashboard and debugging."""
        return {
            "path": self.path,
            "final_stage": self.stage.value,
            "answers": self.answers,
            "callback_at": self.callback_at.isoformat() if self.callback_at else None,
            "end_reason": self.end_reason,
            "tool_errors": self.tool_errors,
        }


def overview(questions: list[str]) -> str:
    """Stage overview for the system instruction."""
    qs = "; ".join(questions) if questions else "none configured"
    return f"""Conversation steps (the system tracks which step you are in):
1. Qualify: learn what they need. Questions to cover, one at a time: {qs}. Save answers with save_qualification.
2. Schedule: call check_calendar_availability, offer up to three times, then book_calendar_appointment.
3. Wrap up: confirm, say goodbye, then call end_call.
At any point: schedule_callback if they want a call back later, transfer_to_human if they ask for a person,
end_call with reason not_interested or wrong_number if they decline.
Every tool result includes "next": that is your current step. Follow it, and never call a tool it rules out."""


def parse_future(iso: str, now: datetime | None = None) -> datetime | None:
    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if when.tzinfo is None:
        return None  # require an explicit offset so the time is unambiguous
    return when if when > (now or datetime.now(timezone.utc)) else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
