import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def normalize_phone(raw: str) -> str:
    """Normalise to E.164; bare 10-digit numbers are assumed to be US/CA."""
    digits = re.sub(r"[^\d+]", "", raw.strip())
    if not digits.startswith("+"):
        digits = "+1" + digits if len(digits) == 10 else "+" + digits
    if not E164.match(digits):
        raise ValueError(f"invalid phone number: {raw!r}")
    return digits


class LeadIn(BaseModel):
    phone: str
    full_name: str | None = Field(default=None, max_length=120)
    email: EmailStr | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    trigger_call_immediately: bool = False

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v)


class LeadOut(BaseModel):
    contact_id: UUID
    stage: str
    call_log_id: UUID | None = None
    dialed: bool = False


class PostCallExtraction(BaseModel):
    outcome: Literal["booked", "voicemail", "follow_up_requested", "transferred", "not_interested"]
    sentiment: Literal["positive", "neutral", "negative"]
    summary: str = Field(description="Concise 2-sentence summary of caller needs and objections")
    requested_callback_iso: str | None = Field(
        default=None,
        description="ISO 8601 timestamp with offset if the caller asked to be called back at a specific time",
    )
    extracted_custom_fields: dict[str, Any] = Field(default_factory=dict)


class CallContext(BaseModel):
    """Everything the pipeline needs to know about the call it is running."""

    contact_id: UUID
    call_log_id: UUID
    direction: Literal["inbound", "outbound"]
    transport: Literal["webrtc", "twilio"]
    provider_call_id: str | None = None
