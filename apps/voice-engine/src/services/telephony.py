"""Twilio adapter: outbound dialer, TwiML builders, live-call bridge, webhook signature check."""

import asyncio
from functools import lru_cache
from urllib.parse import urlencode
from uuid import UUID

from loguru import logger
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, Dial, VoiceResponse

from src import db
from src.config import get_settings


class TelephonyDisabled(RuntimeError):
    pass


@lru_cache
def _client() -> Client:
    s = get_settings()
    if not (s.twilio_account_sid and s.twilio_auth_token):
        raise TelephonyDisabled("Twilio credentials are not configured")
    return Client(s.twilio_account_sid, s.twilio_auth_token)


def telephony_enabled() -> bool:
    return get_settings().telephony_provider == "twilio"


def _webhook(path: str, **query: str) -> str:
    base = get_settings().public_voice_webhook_base_url.rstrip("/")
    return f"{base}{path}" + (f"?{urlencode(query)}" if query else "")


def stream_twiml(contact_id: UUID, call_log_id: UUID, direction: str) -> str:
    response = VoiceResponse()
    connect = Connect()
    stream = connect.stream(url=get_settings().public_ws_url)
    stream.parameter(name="contact_id", value=str(contact_id))
    stream.parameter(name="call_log_id", value=str(call_log_id))
    stream.parameter(name="direction", value=direction)
    response.append(connect)
    return str(response)


def hangup_twiml() -> str:
    response = VoiceResponse()
    response.hangup()
    return str(response)


async def initiate_outbound_call(contact_id: UUID, call_log_id: UUID, phone: str) -> str | None:
    """Place the call; Twilio fetches /api/calls/outbound-xml once answered. Returns CallSid."""
    if not telephony_enabled():
        logger.info(f"TELEPHONY_PROVIDER=webrtc_only; not dialing {phone} (call_log {call_log_id})")
        return None
    s = get_settings()
    ids = {"contact_id": str(contact_id), "call_log_id": str(call_log_id)}

    def _create() -> str:
        call = _client().calls.create(
            to=phone,
            from_=s.twilio_phone_number,
            url=_webhook("/api/calls/outbound-xml", **ids),
            method="POST",
            machine_detection="DetectMessageEnd",
            record=True,
            recording_status_callback=_webhook("/api/calls/status", **ids),
            recording_status_callback_event=["completed"],
            status_callback=_webhook("/api/calls/status", **ids),
            status_callback_event=["completed"],
        )
        return call.sid

    sid = await asyncio.to_thread(_create)
    await db.set_provider_call_id(call_log_id, sid)
    logger.info(f"Dialing {phone}: CallSid={sid}")
    return sid


async def bridge_call(provider_call_id: str, human_phone: str) -> None:
    """Redirect the live call out of the media stream and into a <Dial> to the human."""
    response = VoiceResponse()
    response.say("Connecting you now.")
    dial = Dial(caller_id=get_settings().twilio_phone_number or None, timeout=25)
    dial.number(human_phone)
    response.append(dial)
    twiml = str(response)
    await asyncio.to_thread(lambda: _client().calls(provider_call_id).update(twiml=twiml))


def is_valid_signature(url: str, params: dict[str, str], signature: str | None) -> bool:
    s = get_settings()
    if not s.validate_twilio_signature:
        return True
    if not (signature and s.twilio_auth_token):
        return False
    return RequestValidator(s.twilio_auth_token).validate(url, params, signature)
