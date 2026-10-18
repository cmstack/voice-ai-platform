"""Twilio webhooks, the Twilio Media Streams WebSocket, and the browser WebRTC signalling API."""

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from loguru import logger
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pydantic import BaseModel, ConfigDict, Field

from src import db
from src.config import get_settings
from src.pipeline.post_call import NO_CONVERSATION, run_post_call
from src.pipeline.voice_pipeline import VoiceSession
from src.schemas import CallContext, normalize_phone
from src.services import telephony
from src.services.auth import require_user

router = APIRouter(tags=["calls"])

WEBRTC_TEST_INBOUND_PHONE = "+15550001000"
WEBRTC_TEST_OUTBOUND_PHONE = "+15550002000"
FAILED_CALL_STATUSES = {"no-answer", "busy", "failed", "canceled"}

_sessions: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _sessions.add(task)
    task.add_done_callback(_sessions.discard)


def _twiml(xml: str) -> Response:
    return Response(content=xml, media_type="application/xml")


async def twilio_form(request: Request) -> dict[str, str]:
    """Parse a Twilio webhook and reject it unless X-Twilio-Signature matches the public URL."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    public = get_settings().public_voice_webhook_base_url.rstrip("/") + request.url.path
    if request.url.query:
        public += "?" + request.url.query
    if not telephony.is_valid_signature(public, form, request.headers.get("X-Twilio-Signature")):
        raise HTTPException(403, "invalid Twilio signature")
    return form


# --- Twilio webhooks ----------------------------------------------------------


@router.post("/api/calls/inbound")
async def inbound_call(form: dict = Depends(twilio_form)) -> Response:
    phone = normalize_phone(form.get("From", ""))
    contact, call_log_id, _ = await db.inbound_call_lookup(phone, form.get("CallSid"))
    return _twiml(telephony.stream_twiml(contact["id"], call_log_id, "inbound"))


@router.post("/api/calls/outbound-xml")
async def outbound_answered(contact_id: UUID, call_log_id: UUID, form: dict = Depends(twilio_form)) -> Response:
    answered_by = form.get("AnsweredBy", "")
    if answered_by.startswith("machine") or answered_by == "fax":
        logger.info(f"Call {call_log_id} reached {answered_by}; hanging up and scheduling retry")
        call = CallContext(contact_id=contact_id, call_log_id=call_log_id, direction="outbound", transport="twilio")
        _spawn(run_post_call(call, [], started_at=_now(), duration_seconds=None, booked=False,
                             transferred=False, tz="UTC", forced_outcome=NO_CONVERSATION))
        return _twiml(telephony.hangup_twiml())
    return _twiml(telephony.stream_twiml(contact_id, call_log_id, "outbound"))


@router.post("/api/calls/status")
async def call_status(
    contact_id: UUID | None = None, call_log_id: UUID | None = None, form: dict = Depends(twilio_form)
) -> Response:
    sid = form.get("CallSid", "")
    duration = form.get("CallDuration") or form.get("RecordingDuration")
    recording = form.get("RecordingUrl")
    log = await db.update_call_status(sid, int(duration) if duration else None, f"{recording}.mp3" if recording else None)
    if (
        log and log["outcome"] is None and form.get("CallStatus") in FAILED_CALL_STATUSES
        and contact_id and call_log_id
    ):
        call = CallContext(contact_id=contact_id, call_log_id=call_log_id, direction="outbound", transport="twilio")
        _spawn(run_post_call(call, [], started_at=_now(), duration_seconds=0, booked=False,
                             transferred=False, tz="UTC", forced_outcome=NO_CONVERSATION))
    return Response(status_code=204)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- Twilio Media Streams -------------------------------------------------------


@router.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    transport_type, call_data = await parse_telephony_websocket(websocket)
    if transport_type != "twilio":
        logger.warning(f"Rejecting {transport_type} media stream; only Twilio is supported")
        await websocket.close(code=1008)
        return
    params = call_data.get("body", {}) or {}
    try:
        call_log_id = UUID(params["call_log_id"])
        contact_id = UUID(params["contact_id"])
    except (KeyError, ValueError):
        await websocket.close(code=1008)
        return
    log = await db.get_call_log(call_log_id)
    # The stream URL is public, so only accept streams for a live call row we created.
    if not log or log["contact_id"] != contact_id or log["outcome"] is not None:
        logger.warning(f"Rejecting media stream for unknown/finished call {call_log_id}")
        await websocket.close(code=1008)
        return

    s = get_settings()
    call_sid = call_data["call_id"]
    if log["provider_call_id"] != call_sid:
        await db.set_provider_call_id(call_log_id, call_sid)
    serializer = TwilioFrameSerializer(
        stream_sid=call_data["stream_id"],
        call_sid=call_sid,
        account_sid=s.twilio_account_sid or None,
        auth_token=s.twilio_auth_token or None,
    )
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False, serializer=serializer
        ),
    )
    call = CallContext(
        contact_id=contact_id,
        call_log_id=call_log_id,
        direction="inbound" if params.get("direction") == "inbound" else "outbound",
        transport="twilio",
        provider_call_id=call_sid,
    )
    await VoiceSession(transport, call, enable_rtvi=False).run()


# --- Browser WebRTC (the $0 test path) -----------------------------------------


class WebRTCOffer(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sdp: str
    type: str
    pc_id: str | None = None
    restart_pc: bool | None = None
    request_data: dict | None = Field(default=None, alias="requestData")


class WebRTCPatch(BaseModel):
    pc_id: str
    candidates: list[dict]


_webrtc = SmallWebRTCRequestHandler()


async def _prepare_webrtc_call(request_data: dict) -> CallContext:
    direction = "outbound" if request_data.get("direction") == "outbound" else "inbound"
    contact_id = request_data.get("contact_id")
    if direction == "inbound" and not contact_id:
        contact, call_log_id, _ = await db.inbound_call_lookup(WEBRTC_TEST_INBOUND_PHONE, None, direction="webrtc")
        return CallContext(contact_id=contact["id"], call_log_id=call_log_id, direction="inbound", transport="webrtc")
    if contact_id:
        contact = await db.get_contact(UUID(contact_id))
        if contact is None:
            raise HTTPException(404, "contact not found")
    else:
        contact = await db.upsert_lead(WEBRTC_TEST_OUTBOUND_PHONE, "Browser Test Lead", None, {"source": "simulator"})
    if direction == "outbound":
        call_log_id = await db.start_outbound_call(contact["id"], direction="webrtc")
    else:
        _, call_log_id, _ = await db.inbound_call_lookup(contact["phone"], None, direction="webrtc")
    return CallContext(contact_id=contact["id"], call_log_id=call_log_id, direction=direction, transport="webrtc")


@router.post("/api/webrtc/offer")
async def webrtc_offer(offer: WebRTCOffer, _user: dict = Depends(require_user)) -> dict:
    call: CallContext | None = None
    if not offer.pc_id:  # renegotiations reuse the existing session
        call = await _prepare_webrtc_call(offer.request_data or {})

    async def on_connection(connection: SmallWebRTCConnection):
        assert call is not None
        call.provider_call_id = connection.pc_id
        await db.set_provider_call_id(call.call_log_id, connection.pc_id)
        transport = SmallWebRTCTransport(
            webrtc_connection=connection,
            params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
        )
        _spawn(VoiceSession(transport, call, enable_rtvi=True).run())

    answer = await _webrtc.handle_web_request(
        SmallWebRTCRequest(sdp=offer.sdp, type=offer.type, pc_id=offer.pc_id,
                           restart_pc=offer.restart_pc, request_data=offer.request_data),
        on_connection,
    )
    if answer is None:
        raise HTTPException(500, "no SDP answer")
    if call is not None:
        answer["call_log_id"] = str(call.call_log_id)
        answer["contact_id"] = str(call.contact_id)
    return answer


@router.patch("/api/webrtc/offer")
async def webrtc_ice(patch: WebRTCPatch, _user: dict = Depends(require_user)) -> dict:
    candidates = [
        IceCandidate(candidate=c["candidate"], sdp_mid=c.get("sdp_mid", c.get("sdpMid")),
                     sdp_mline_index=c.get("sdp_mline_index", c.get("sdpMLineIndex")))
        for c in patch.candidates
    ]
    await _webrtc.handle_patch_request(SmallWebRTCPatchRequest(pc_id=patch.pc_id, candidates=candidates))
    return {"status": "success"}


async def shutdown_webrtc() -> None:
    await _webrtc.close()
    for task in list(_sessions):
        task.cancel()
