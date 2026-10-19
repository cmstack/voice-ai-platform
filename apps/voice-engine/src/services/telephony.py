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
from src.services import dial_guard
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


def stream_url(call_log_id: UUID) -> str:
    """Per-call stream URL. Twilio signs the full URL, so every call gets a different signature
    and a captured handshake header can't be replayed for another call."""
    return f"{get_settings().public_ws_url}?{urlencode({'call_log_id': str(call_log_id)})}"


def stream_twiml(contact_id: UUID, call_log_id: UUID, direction: str) -> str:
    response = VoiceResponse()
    connect = Connect()
    stream = connect.stream(url=stream_url(call_log_id))
    stream.parameter(name="contact_id", value=str(contact_id))
    stream.parameter(name="call_log_id", value=str(call_log_id))
    stream.parameter(name="direction", value=direction)
    response.append(connect)
    return str(response)


def busy_twiml() -> str:
    response = VoiceResponse()
    response.say("Sorry, all of our lines are busy right now. Please call back in a few minutes.")
    response.hangup()
    return str(response)


def hangup_twiml() -> str:
    response = VoiceResponse()
    response.hangup()
    return str(response)


def mask_phone(phone: str) -> str:
    """Keep logs useful without writing full phone numbers (PII) to them."""
    return phone[:-4].translate(str.maketrans("0123456789", "**********")) + phone[-4:] if len(phone) > 4 else "****"


async def initiate_outbound_call(contact_id: UUID, call_log_id: UUID, phone: str) -> str | None:
    """Place the call; Twilio fetches /api/calls/outbound-xml once answered. Returns CallSid.

    Every real dial passes `dial_guard.check` first and raises DialBlocked when policy forbids it.
    """
    if not telephony_enabled():
        logger.info(f"TELEPHONY_PROVIDER=webrtc_only; not dialing {mask_phone(phone)} (call_log {call_log_id})")
        return None
    contact = await db.get_contact(contact_id)
    config = await db.get_agent_config()
    if contact is None:
        raise dial_guard.DialBlocked("contact no longer exists")
    await dial_guard.check(contact, config)

    s = get_settings()
    ids = {"contact_id": str(contact_id), "call_log_id": str(call_log_id)}
    recording = (
        {
            "record": True,
            "recording_status_callback": _webhook("/api/calls/status", **ids),
            "recording_status_callback_event": ["completed"],
        }
        if config.get("record_calls")
        else {}
    )

    def _create() -> str:
        call = _client().calls.create(
            to=contact["phone"],
            from_=s.twilio_phone_number,
            url=_webhook("/api/calls/outbound-xml", **ids),
            method="POST",
            machine_detection="DetectMessageEnd",
            status_callback=_webhook("/api/calls/status", **ids),
            status_callback_event=["completed"],
            **recording,
        )
        return call.sid

    sid = await asyncio.to_thread(_create)
    await db.set_provider_call_id(call_log_id, sid)
    logger.info(f"Dialing {mask_phone(contact['phone'])}: CallSid={sid}")
    return sid


async def bridge_call(provider_call_id: str, human_phone: str) -> None:
    """Redirect the live call out of the media stream and into a <Dial> to the human.

    The transfer number is a real dial too, so it must be in the allowed calling countries.
    """
    if not dial_guard.country_allowed(human_phone, get_settings().allowed_dial_country_codes):
        raise dial_guard.DialBlocked(f"transfer number {mask_phone(human_phone)} is outside the allowed calling countries")
    response = VoiceResponse()
    response.say("Connecting you now.")
    dial = Dial(caller_id=get_settings().twilio_phone_number or None, timeout=25)
    dial.number(human_phone)
    response.append(dial)
    twiml = str(response)
    await asyncio.to_thread(lambda: _client().calls(provider_call_id).update(twiml=twiml))


def is_valid_ws_signature(signature: str | None, query: str = "") -> bool:
    """Validate X-Twilio-Signature on the Media Streams WebSocket handshake.

    Twilio signs the stream URL it was given in our TwiML (`stream_url()`, including its
    per-call query string) with no POST params.
    Set VALIDATE_TWILIO_WS_SIGNATURE=false only to debug a proxy that rewrites the URL.
    """
    s = get_settings()
    if not (s.validate_twilio_signature and s.validate_twilio_ws_signature):
        return True
    if not (signature and s.twilio_auth_token):
        return False
    validator = RequestValidator(s.twilio_auth_token)
    url = s.public_ws_url + (f"?{query}" if query else "")
    # Twilio's docs require validating this header but don't spell out the scheme of the signed
    # URL, so accept the wss:// form we sent and its https:// equivalent.
    https_url = url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
    return validator.validate(url, {}, signature) or validator.validate(https_url, {}, signature)


def is_valid_signature(url: str, params: dict[str, str], signature: str | None) -> bool:
    s = get_settings()
    if not s.validate_twilio_signature:
        return True
    if not (signature and s.twilio_auth_token):
        return False
    return RequestValidator(s.twilio_auth_token).validate(url, params, signature)
