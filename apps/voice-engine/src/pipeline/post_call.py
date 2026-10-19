"""Post-call analysis and the deterministic pipeline-stage state machine."""

from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from src import db
from src.pipeline.flow import FlowState
from src.pipeline.sanitize import clean_fields
from src.schemas import CallContext, PostCallExtraction
from src.services import llm

MAX_VOICEMAIL_RETRIES = 3
VOICEMAIL_RETRY_DELAY = timedelta(hours=4)
DEFAULT_FOLLOW_UP_DELAY = timedelta(hours=24)
TERMINAL_STAGES = {"appointment_booked", "transferred_to_human"}


def _parse_callback(iso: str | None, now: datetime) -> datetime | None:
    if not iso:
        return None
    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when if when > now else None


def decide_stage(contact: dict[str, Any], extraction: PostCallExtraction, now: datetime) -> dict[str, Any] | None:
    """Columns to update on `contacts`, or None to leave the stage alone."""
    if contact["stage"] in TERMINAL_STAGES:
        return None
    match extraction.outcome:
        case "voicemail":
            if contact["retry_count"] < MAX_VOICEMAIL_RETRIES:
                return {
                    "stage": "no_answer_retry",
                    "retry_count": contact["retry_count"] + 1,
                    "next_follow_up_at": now + VOICEMAIL_RETRY_DELAY,
                }
            return {"stage": "not_interested", "next_follow_up_at": None}
        case "follow_up_requested":
            return {
                "stage": "follow_up_requested",
                "next_follow_up_at": _parse_callback(extraction.requested_callback_iso, now)
                or now + DEFAULT_FOLLOW_UP_DELAY,
            }
        case "not_interested":
            return {"stage": "not_interested", "next_follow_up_at": None}
        case _:
            # booked / transferred: the in-call tools already moved the card
            return None


NO_CONVERSATION = PostCallExtraction(
    outcome="voicemail", sentiment="neutral", summary="No conversation took place (no answer or voicemail)."
)


def apply_flow(extraction: PostCallExtraction, flow: FlowState | None) -> PostCallExtraction:
    """What the conversation flow recorded during the call outranks the transcript classifier."""
    if flow is None:
        return extraction
    if flow.answers:
        extraction.extracted_custom_fields = {**extraction.extracted_custom_fields, **flow.answers}
    if flow.callback_at is not None:
        extraction.outcome = "follow_up_requested"
        extraction.requested_callback_iso = flow.callback_at.isoformat()
    elif flow.end_reason in ("not_interested", "wrong_number"):
        extraction.outcome = "not_interested"
    elif flow.end_reason == "voicemail":
        extraction.outcome = "voicemail"
    return extraction


async def analyse(
    transcript: list[dict[str, Any]],
    *,
    started_at: datetime,
    tz: str,
    booked: bool,
    transferred: bool,
    flow: FlowState | None = None,
) -> PostCallExtraction:
    if not any(t["role"] == "user" and t["content"].strip() for t in transcript):
        return NO_CONVERSATION.model_copy(deep=True)
    try:
        extraction = await llm.extract(
            transcript, call_time=started_at, timezone=tz, booked=booked, transferred=transferred
        )
    except Exception:
        logger.exception("Post-call extraction failed; falling back to a neutral follow-up")
        extraction = PostCallExtraction(
            outcome="follow_up_requested", sentiment="neutral", summary="Automatic call analysis failed."
        )
    # Tool results are ground truth; never let the classifier contradict them.
    extraction = apply_flow(extraction, flow)
    if booked:
        extraction.outcome = "booked"
    elif transferred:
        extraction.outcome = "transferred"
    return extraction


async def run_post_call(
    call: CallContext,
    transcript: list[dict[str, Any]],
    *,
    started_at: datetime,
    duration_seconds: int | None,
    booked: bool,
    transferred: bool,
    tz: str,
    forced_outcome: PostCallExtraction | None = None,
    flow: FlowState | None = None,
) -> dict[str, Any]:
    extraction = forced_outcome or await analyse(
        transcript, started_at=started_at, tz=tz, booked=booked, transferred=transferred, flow=flow
    )
    # Extracted facts come from caller speech: clean them before they are stored and later re-used in prompts.
    extraction.extracted_custom_fields = clean_fields(extraction.extracted_custom_fields)
    extracted_data = extraction.model_dump()
    if flow is not None:
        extracted_data["flow"] = flow.summary()
    now = datetime.now(timezone.utc)
    contact = await db.finalize_call(
        call_log_id=call.call_log_id,
        contact_id=call.contact_id,
        transcript=transcript,
        duration_seconds=duration_seconds,
        outcome=extraction.outcome,
        sentiment=extraction.sentiment,
        summary=extraction.summary,
        extracted_data=extracted_data,
        custom_fields=extraction.extracted_custom_fields,
        stage_update_fn=lambda c: decide_stage(c, extraction, now),
    )
    logger.info(f"Call {call.call_log_id}: outcome={extraction.outcome} stage={contact['stage']}")
    return contact
