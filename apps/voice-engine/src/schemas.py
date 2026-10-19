import re
from datetime import datetime
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
    # Prior express consent to be called. Required before the system will auto-dial this number.
    consent: bool = False
    consent_source: str | None = Field(default=None, max_length=200, description="Where consent was given, e.g. 'website form v3'")

    @field_validator("custom_fields")
    @classmethod
    def _small_custom_fields(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > 30 or any(len(str(k)) > 60 or len(str(val)) > 500 for k, val in v.items()):
            raise ValueError("custom_fields: at most 30 entries, keys up to 60 chars, values up to 500 chars")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v)


class LeadOut(BaseModel):
    contact_id: UUID
    stage: str
    call_log_id: UUID | None = None
    dialed: bool = False
    blocked_reason: str | None = None
    next_attempt_at: datetime | None = None


class _PostCallBase(BaseModel):
    outcome: Literal["booked", "voicemail", "follow_up_requested", "transferred", "not_interested"]
    sentiment: Literal["positive", "neutral", "negative"]
    summary: str = Field(description="Concise 2-sentence summary of caller needs and objections")
    requested_callback_iso: str | None = Field(
        default=None,
        description="ISO 8601 timestamp with offset if the caller asked to be called back at a specific time",
    )


class PostCallExtraction(_PostCallBase):
    extracted_custom_fields: dict[str, Any] = Field(default_factory=dict)


class CustomField(BaseModel):
    key: str = Field(description="Short snake_case name, e.g. budget, timeline, company")
    value: str


class PostCallExtractionOutput(_PostCallBase):
    """Model-facing schema. Structured outputs steer an open-ended object to `{}`, so facts come back as pairs."""

    extracted_custom_fields: list[CustomField] = Field(
        default_factory=list, description="Useful facts the caller stated (budget, goals, timeline, company, service interest)"
    )

    def to_extraction(self) -> PostCallExtraction:
        fields = {f.key.strip(): f.value.strip() for f in self.extracted_custom_fields if f.key.strip() and f.value.strip()}
        return PostCallExtraction(**self.model_dump(exclude={"extracted_custom_fields"}), extracted_custom_fields=fields)


class CallContext(BaseModel):
    """Everything the pipeline needs to know about the call it is running."""

    contact_id: UUID
    call_log_id: UUID
    direction: Literal["inbound", "outbound"]
    transport: Literal["webrtc", "twilio"]
    provider_call_id: str | None = None
