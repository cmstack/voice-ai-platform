"""Post-call structured extraction with Claude on Amazon Bedrock."""

from datetime import datetime
from functools import lru_cache
from typing import Any

from anthropic import AsyncAnthropicBedrockMantle

from src.config import get_settings
from src.schemas import PostCallExtraction

SYSTEM = """You analyse transcripts of sales/receptionist phone calls made by an AI agent.
Classify the call outcome:
- booked: an appointment was successfully booked during the call.
- voicemail: an answering machine/voicemail, or the callee never meaningfully engaged.
- follow_up_requested: the person asked to be called back or wants to think and reconnect later.
- transferred: the call was handed to a human specialist.
- not_interested: the person declined, asked not to be called, or is clearly not a fit.
Write a concise 2-sentence summary of the caller's needs and objections.
If a specific callback time was requested, give it as an ISO 8601 timestamp with UTC offset, resolved
against the call time and timezone given. Put any useful facts (budget, goals, timeline, company,
service interest) in extracted_custom_fields using short snake_case keys."""


@lru_cache
def _client() -> AsyncAnthropicBedrockMantle:
    s = get_settings()
    return AsyncAnthropicBedrockMantle(
        aws_region=s.aws_region,
        aws_access_key=s.aws_access_key_id or None,
        aws_secret_key=s.aws_secret_access_key or None,
        aws_session_token=s.aws_session_token or None,
    )


def format_transcript(transcript: list[dict[str, Any]]) -> str:
    who = {"user": "Caller", "assistant": "Agent"}
    return "\n".join(f"{who.get(t['role'], t['role'])}: {t['content']}" for t in transcript)


async def extract(
    transcript: list[dict[str, Any]], *, call_time: datetime, timezone: str, booked: bool, transferred: bool
) -> PostCallExtraction:
    facts = [f"Call started: {call_time.isoformat()} (timezone {timezone})."]
    if booked:
        facts.append("System note: the booking tool succeeded during this call.")
    if transferred:
        facts.append("System note: the call was transferred to a human.")
    response = await _client().messages.parse(
        model=get_settings().bedrock_claude_model_id,
        max_tokens=2000,
        system=SYSTEM,
        output_config={"effort": "low"},
        messages=[{
            "role": "user",
            "content": "\n".join(facts) + "\n\n<transcript>\n" + format_transcript(transcript) + "\n</transcript>",
        }],
        output_format=PostCallExtraction,
    )
    if response.parsed_output is None:
        raise RuntimeError(f"extraction returned no structured output (stop_reason={response.stop_reason})")
    return response.parsed_output
