"""Second security review: per-call stream signatures, capacity caps, production detection, logging,
recording proxy, retention, least privilege, live admin revocation, and crash-free edge cases."""

import io
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
import respx
from loguru import logger
from twilio.request_validator import RequestValidator

from src import db
from src.config import Settings, get_settings
from src.logging_config import configure as configure_logging
from src.pipeline import tools
from src.pipeline.tools import ToolRuntime, run_tool
from src.routers import calls, recordings
from src.schemas import CallContext
from src.services import auth, dial_guard, retention, telephony
from src.services.rate_limit import SlidingWindowLimiter, all_calls

from .conftest import TEST_PHONE_PREFIX, make_client, make_token

TOKEN = "test-auth-token"
BASE = "https://voice.example.test"
PHONE = TEST_PHONE_PREFIX + "951"
ADMIN_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def twilio(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "twilio_auth_token", TOKEN)
    monkeypatch.setattr(s, "twilio_account_sid", "AC_test")
    monkeypatch.setattr(s, "public_voice_webhook_base_url", BASE)
    monkeypatch.setattr(s, "validate_twilio_signature", True)


def signed(path: str, form: dict) -> dict:
    return {"X-Twilio-Signature": RequestValidator(TOKEN).compute_signature(BASE + path, form)}


# --- M1: per-call stream signatures ----------------------------------------------------


def test_stream_signature_is_per_call(twilio):
    a, b = uuid4(), uuid4()
    url_a = telephony.stream_url(a)
    sig_a = RequestValidator(TOKEN).compute_signature(url_a, {})
    assert telephony.is_valid_ws_signature(sig_a, f"call_log_id={a}")
    assert not telephony.is_valid_ws_signature(sig_a, f"call_log_id={b}")  # can't be replayed for another call
    assert not telephony.is_valid_ws_signature(sig_a, "")


def test_second_stream_for_same_call_is_rejected(twilio, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    call_log_id, contact_id = uuid4(), uuid4()

    async def live_log(_id):
        return {"contact_id": contact_id, "outcome": None, "provider_call_id": "CA9"}

    monkeypatch.setattr(calls.db, "get_call_log", live_log)
    calls._live_streams.add(call_log_id)  # a stream for this call is already open
    try:
        app = FastAPI(); app.include_router(calls.router)
        sig = RequestValidator(TOKEN).compute_signature(telephony.stream_url(call_log_id), {})
        with TestClient(app) as tc, tc.websocket_connect(f"/ws/voice?call_log_id={call_log_id}",
                                                          headers={"X-Twilio-Signature": sig}) as ws:
            ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
            ws.send_json({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA9", "accountSid": "AC1",
                          "customParameters": {"contact_id": str(contact_id), "call_log_id": str(call_log_id)}}})
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_text()
        assert exc.value.code == 1008
    finally:
        calls._live_streams.discard(call_log_id)


# --- M2: capacity, bookings, transfers ---------------------------------------------------


async def test_inbound_gets_busy_message_when_lines_are_full(twilio, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_concurrent_calls", 1)
    assert all_calls.try_acquire()
    form = {"From": PHONE, "CallSid": "CA_busy"}
    async with make_client(calls.router) as c:
        r = await c.post("/api/calls/inbound", data=form, headers=signed("/api/calls/inbound", form))
    assert "lines are busy" in r.text and "<Hangup" in r.text and "<Stream" not in r.text


async def test_browser_call_refused_when_lines_are_full(auth_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_concurrent_calls", 1)
    assert all_calls.try_acquire()
    async with make_client(calls.router) as c:
        r = await c.post("/api/webrtc/offer", headers=auth_headers, json={"sdp": "v=0", "type": "offer"})
    assert r.status_code == 429 and "line is busy" in r.json()["detail"]


async def test_dial_guard_defers_when_lines_are_full(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_concurrent_calls", 1)
    monkeypatch.setattr(dial_guard, "within_business_hours", lambda *_: True)
    contact = await db.upsert_lead(PHONE, "Kai", None, {}, consent_source="test")
    assert all_calls.try_acquire()
    with pytest.raises(dial_guard.DialBlocked) as exc:
        await dial_guard.check(contact, await db.get_agent_config())
    assert exc.value.reason == "too many calls in progress" and exc.value.retry_at


@pytest.fixture
async def rt():
    c = await db.upsert_lead(PHONE, "Kai", None, {}, consent_source="test")
    log_id = await db.start_outbound_call(c["id"])
    return ToolRuntime(call=CallContext(contact_id=c["id"], call_log_id=log_id, direction="outbound", transport="webrtc"),
                       config=await db.get_agent_config(), contact=c)


async def test_hourly_booking_cap(rt, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bookings_per_hour", 0)
    slot = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
    rt.offered_slots[slot.isoformat()] = slot
    rt.flow.enter(tools.Stage.SCHEDULE)
    out = await run_tool(rt, "book_calendar_appointment", tools.book_calendar_appointment)(
        slot_iso=slot.isoformat(), attendee_name="Kai Lin", attendee_email="kai@example.com")
    assert out["success"] is False and "unavailable right now" in out["error"]


async def test_foreign_transfer_number_is_refused(rt):
    rt.config["human_transfer_phone"] = "+882123456789"  # international premium-rate style number
    out = await run_tool(rt, "transfer_to_human", tools.transfer_to_human)(reason="wants a person")
    assert out["success"] is False and not rt.transferred
    with pytest.raises(dial_guard.DialBlocked):
        await telephony.bridge_call("CA1", "+882123456789")


# --- M3/M4/L8: production detection, logging, API docs -------------------------------------


@pytest.mark.parametrize("overrides,expected", [
    ({}, False),
    ({"telephony_provider": "twilio"}, True),
    ({"public_voice_webhook_base_url": "https://voice.example.com"}, True),
    ({"supabase_url": "https://abc.supabase.co"}, True),
    ({"supabase_url": "http://host.docker.internal:54321"}, False),
    ({"app_env": "development", "telephony_provider": "twilio"}, False),
    ({"app_env": "production"}, True),
])
def test_production_is_auto_detected(overrides, expected):
    assert Settings(_env_file=None, **overrides).is_production is expected


def test_exposed_deploy_with_defaults_refuses_to_start():
    from src.config import InsecureConfiguration
    with pytest.raises(InsecureConfiguration):
        Settings(_env_file=None, public_voice_webhook_base_url="https://voice.example.com").check_security()


def test_debug_logging_is_flagged_and_docs_off_in_production():
    s = Settings(_env_file=None, log_level="DEBUG")
    assert any("LOG_LEVEL" in p for p in s.security_problems())
    assert Settings(_env_file=None).api_docs_enabled is True
    assert Settings(_env_file=None, app_env="production").api_docs_enabled is False


def test_info_logging_drops_debug_prompts():
    buf = io.StringIO()
    configure_logging("INFO")
    sink = logger.add(buf, level="INFO")
    try:
        logger.debug("Using system instruction: phone +15551234567")
        logger.info("visible")
    finally:
        logger.remove(sink)
    assert "visible" in buf.getvalue() and "+15551234567" not in buf.getvalue()


# --- M5/L14: recording proxy and retention -----------------------------------------------------


REC = "https://api.twilio.com/2010-04-01/Accounts/AC_test/Recordings/RE" + "a" * 32 + ".mp3"


async def _log_with_recording(url: str, days_old: int = 0):
    c = await db.upsert_lead(PHONE, "Kai", None, {})
    log_id = await db.start_outbound_call(c["id"])
    await db.pool().execute(
        "UPDATE call_logs SET recording_url=$2, transcript='[{\"role\":\"user\",\"content\":\"hi\"}]', "
        "extracted_data='{\"flow\":{}}', summary='kept', created_at=NOW() - make_interval(days => $3) WHERE id=$1",
        log_id, url, days_old)
    return log_id


@respx.mock
async def test_recording_proxy_streams_with_twilio_auth(auth_headers, twilio):
    log_id = await _log_with_recording(REC)
    route = respx.get(REC).mock(return_value=httpx.Response(200, content=b"ID3audio", headers={"content-type": "audio/mpeg"}))
    async with make_client(recordings.router) as c:
        r = await c.get(f"/api/recordings/{log_id}", headers=auth_headers)
    assert r.status_code == 200 and r.content == b"ID3audio" and r.headers["cache-control"] == "private, no-store"
    assert route.calls[0].request.headers["authorization"].startswith("Basic ")


async def test_recording_proxy_refuses_non_twilio_urls(auth_headers, twilio):
    log_id = await _log_with_recording("http://169.254.169.254/latest/meta-data/")
    async with make_client(recordings.router) as c:
        r = await c.get(f"/api/recordings/{log_id}", headers=auth_headers)
        unauth = await c.get(f"/api/recordings/{log_id}")
    assert r.status_code == 404 and unauth.status_code == 401


async def test_retention_purges_only_old_call_content(monkeypatch, pool):
    monkeypatch.setattr(get_settings(), "retention_days", 30)
    old = await _log_with_recording(REC, days_old=45)
    new = await _log_with_recording(REC, days_old=1)
    deleted = []
    monkeypatch.setattr(retention, "_delete_twilio_recording", lambda url: _record(deleted, url))
    assert await retention.purge_once() >= 1
    o = await pool.fetchrow("SELECT transcript, recording_url, extracted_data, summary FROM call_logs WHERE id=$1", old)
    n = await pool.fetchrow("SELECT recording_url FROM call_logs WHERE id=$1", new)
    assert (o["transcript"], o["recording_url"], o["extracted_data"], o["summary"]) == ([], None, {}, "kept")
    assert n["recording_url"] == REC and REC in deleted


async def _record(bucket, url):
    bucket.append(url)


# --- L9/L12: least privilege and immediate admin revocation ---------------------------------------


def _rest(token: str | None):
    s = get_settings()
    anon = make_token(app_role=None, role="anon")
    headers = {"apikey": anon}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.get(f"{s.supabase_url}/rest/v1/contacts?select=id&limit=1", headers=headers, timeout=5)


def test_anon_role_has_no_table_access():
    r = _rest(None)
    assert r.status_code in (401, 403) and r.json().get("code") == "42501"  # permission denied


async def test_revoking_admin_takes_effect_immediately(pool):
    token = make_token()  # still carries app_metadata.role = admin
    assert len(_rest(token).json()) == 1
    await pool.execute("UPDATE auth.users SET raw_app_meta_data = raw_app_meta_data - 'role' WHERE id = $1::uuid", ADMIN_ID)
    try:
        auth._role_cache.clear()
        assert _rest(token).json() == []  # RLS: is_admin() checks auth.users
        async with make_client(calls.router) as c:
            r = await c.post("/api/webrtc/offer", headers={"Authorization": f"Bearer {token}"}, json={"sdp": "v=0", "type": "offer"})
        assert r.status_code == 403  # API: live role check
    finally:
        await pool.execute("UPDATE auth.users SET raw_app_meta_data = raw_app_meta_data || '{\"role\":\"admin\"}'::jsonb WHERE id = $1::uuid", ADMIN_ID)
        auth._role_cache.clear()


# --- L11/L13: crash-free edge cases and bounded limiter --------------------------------------------


async def test_withheld_caller_id_is_answered(twilio, pool):
    form = {"From": "Anonymous", "CallSid": "CA_anon_1234567890"}
    async with make_client(calls.router) as c:
        r = await c.post("/api/calls/inbound", data=form, headers=signed("/api/calls/inbound", form))
    assert r.status_code == 200 and "<Stream" in r.text
    phone = await pool.fetchval("SELECT phone FROM contacts WHERE phone LIKE 'anon:%' ORDER BY created_at DESC LIMIT 1")
    await pool.execute("DELETE FROM contacts WHERE phone = $1", phone)
    assert phone == "anon:" + form["CallSid"][-14:]
    assert not dial_guard.country_allowed(phone, ["1"])  # never dialable


async def test_malformed_contact_id_is_422(auth_headers):
    async with make_client(calls.router) as c:
        r = await c.post("/api/webrtc/offer", headers=auth_headers,
                         json={"sdp": "v=0", "type": "offer", "requestData": {"direction": "outbound", "contact_id": "nope"}})
    assert r.status_code == 422


def test_limiter_memory_is_bounded(monkeypatch):
    from src.services import rate_limit
    monkeypatch.setattr(rate_limit, "MAX_TRACKED_KEYS", 50)
    lim = SlidingWindowLimiter()
    for i in range(500):
        lim.allow(f"ip-{i}", 5, now=float(i) / 1000)
    assert len(lim._hits) <= 51
