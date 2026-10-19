import time
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import pytest
from twilio.request_validator import RequestValidator

from src import db
from src.config import get_settings
from src.routers import calls
from src.services import retry_worker, telephony

from .conftest import TEST_PHONE_PREFIX, make_client

TOKEN = "test-auth-token"
BASE = "https://voice.example.test"
CALLER = TEST_PHONE_PREFIX + "601"


@pytest.fixture(autouse=True)
def twilio_settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "twilio_auth_token", TOKEN)
    monkeypatch.setattr(s, "public_voice_webhook_base_url", BASE)
    monkeypatch.setattr(s, "validate_twilio_signature", True)


def signed(path: str, form: dict) -> dict:
    return {"X-Twilio-Signature": RequestValidator(TOKEN).compute_signature(BASE + path, form)}


def stream_params(xml: str) -> dict:
    root = ElementTree.fromstring(xml)
    stream = root.find("./Connect/Stream")
    return {"url": stream.get("url"), **{p.get("name"): p.get("value") for p in stream.findall("Parameter")}}


def test_stream_twiml_shape():
    params = stream_params(telephony.stream_twiml("c-1", "l-1", "outbound"))
    assert params == {"url": "wss://voice.example.test/ws/voice?call_log_id=l-1", "contact_id": "c-1",
                      "call_log_id": "l-1", "direction": "outbound"}


async def test_inbound_rejects_unsigned():
    async with make_client(calls.router) as c:
        r = await c.post("/api/calls/inbound", data={"From": CALLER, "CallSid": "CA1"})
    assert r.status_code == 403


async def test_inbound_upserts_caller_and_returns_stream(pool):
    form = {"From": CALLER, "To": "+15550100000", "CallSid": "CA_inbound_1"}
    async with make_client(calls.router) as c:
        await c.post("/api/calls/inbound", data=form, headers=signed("/api/calls/inbound", form))  # warm-up
        await pool.execute("DELETE FROM contacts WHERE phone = $1", CALLER)
        t0 = time.perf_counter()
        r = await c.post("/api/calls/inbound", data=form, headers=signed("/api/calls/inbound", form))
        elapsed_ms = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    params = stream_params(r.text)
    contact = await pool.fetchrow("SELECT * FROM contacts WHERE phone = $1", CALLER)
    log = await pool.fetchrow("SELECT * FROM call_logs WHERE id = $1::uuid", params["call_log_id"])
    assert params["contact_id"] == str(contact["id"]) and params["direction"] == "inbound"
    assert (log["direction"], log["provider_call_id"]) == ("inbound", "CA_inbound_1")
    assert elapsed_ms < 100, f"inbound webhook took {elapsed_ms:.1f} ms"


async def test_outbound_answered_by_human_streams(pool):
    c = await db.upsert_lead(CALLER, "Ann", None, {})
    log_id = await db.start_outbound_call(c["id"])
    path = f"/api/calls/outbound-xml?contact_id={c['id']}&call_log_id={log_id}"
    form = {"CallSid": "CA_out_1", "AnsweredBy": "human"}
    async with make_client(calls.router) as client:
        r = await client.post(path, data=form, headers=signed(path, form))
    assert stream_params(r.text)["direction"] == "outbound"


async def test_outbound_machine_hangs_up_and_schedules_retry(pool):
    c = await db.upsert_lead(CALLER, "Ann", None, {})
    log_id = await db.start_outbound_call(c["id"])
    path = f"/api/calls/outbound-xml?contact_id={c['id']}&call_log_id={log_id}"
    form = {"CallSid": "CA_out_2", "AnsweredBy": "machine_end_beep"}
    async with make_client(calls.router) as client:
        r = await client.post(path, data=form, headers=signed(path, form))
    assert "<Hangup" in r.text
    for task in list(calls._sessions):
        await task
    row = await pool.fetchrow("SELECT stage::text, retry_count, next_follow_up_at FROM contacts WHERE id = $1", c["id"])
    assert row["stage"] == "no_answer_retry" and row["retry_count"] == 1 and row["next_follow_up_at"] is not None
    assert await pool.fetchval("SELECT outcome FROM call_logs WHERE id = $1", log_id) == "voicemail"


async def test_status_callback_records_duration_and_no_answer(pool):
    c = await db.upsert_lead(CALLER, "Ann", None, {})
    log_id = await db.start_outbound_call(c["id"])
    await db.set_provider_call_id(log_id, "CA_status_1")
    path = f"/api/calls/status?contact_id={c['id']}&call_log_id={log_id}"
    form = {"CallSid": "CA_status_1", "CallStatus": "no-answer", "CallDuration": "0"}
    async with make_client(calls.router) as client:
        r = await client.post(path, data=form, headers=signed(path, form))
        assert r.status_code == 204
        for task in list(calls._sessions):
            await task
        rec = {"CallSid": "CA_status_1", "RecordingUrl": "https://api.twilio.com/rec/RE1", "RecordingDuration": "37"}
        await client.post(path, data=rec, headers=signed(path, rec))
    log = await pool.fetchrow("SELECT * FROM call_logs WHERE id = $1", log_id)
    assert log["outcome"] == "voicemail" and log["recording_url"].endswith("RE1.mp3") and log["duration_seconds"] == 37
    assert await pool.fetchval("SELECT stage::text FROM contacts WHERE id = $1", c["id"]) == "no_answer_retry"


def _ws_start_messages(call_log_id="00000000-0000-0000-0000-000000000000"):
    return [
        {"event": "connected", "protocol": "Call", "version": "1.0.0"},
        {"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA9", "accountSid": "AC1",
                                    "customParameters": {"contact_id": "00000000-0000-0000-0000-000000000000",
                                                         "call_log_id": call_log_id}}},
    ]


def ws_app():
    from fastapi import FastAPI
    app = FastAPI(); app.include_router(calls.router)
    return app


def test_ws_rejects_missing_signature():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    with TestClient(ws_app()) as tc, pytest.raises(WebSocketDisconnect) as exc:
        with tc.websocket_connect("/ws/voice"):
            pass
    assert exc.value.code == 1008


def test_ws_rejects_bad_signature():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    with TestClient(ws_app()) as tc, pytest.raises(WebSocketDisconnect):
        with tc.websocket_connect("/ws/voice", headers={"X-Twilio-Signature": "forged"}):
            pass


def test_ws_signature_accepts_twilio_signed_url():
    url = get_settings().public_ws_url
    sig = RequestValidator(TOKEN).compute_signature(url, {})
    assert url == "wss://voice.example.test/ws/voice"
    assert telephony.is_valid_ws_signature(sig)
    https_sig = RequestValidator(TOKEN).compute_signature("https://voice.example.test/ws/voice", {})
    assert telephony.is_valid_ws_signature(https_sig)
    assert not telephony.is_valid_ws_signature(RequestValidator("other-token").compute_signature(url, {}))


def test_ws_rejects_unknown_call_even_when_signed(monkeypatch):
    # TestClient runs the app on its own event loop, so stub the lookup instead of sharing the pool.
    async def no_such_call(_id):
        return None

    monkeypatch.setattr(calls.db, "get_call_log", no_such_call)
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    unknown = "00000000-0000-0000-0000-000000000000"
    sig = RequestValidator(TOKEN).compute_signature(f"{get_settings().public_ws_url}?call_log_id={unknown}", {})
    with TestClient(ws_app()) as tc, tc.websocket_connect(f"/ws/voice?call_log_id={unknown}", headers={"X-Twilio-Signature": sig}) as ws:
        for m in _ws_start_messages():
            ws.send_json(m)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 1008


# --- retry worker -----------------------------------------------------------

CHICAGO_TUE_10AM = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("when,expected", [
    (CHICAGO_TUE_10AM, True),
    (CHICAGO_TUE_10AM.replace(hour=12), False),          # 7 AM Chicago
    (CHICAGO_TUE_10AM.replace(hour=23, minute=59), True), # 6:59 PM
    (CHICAGO_TUE_10AM.replace(day=21, hour=0), False),    # 7 PM
    (CHICAGO_TUE_10AM.replace(day=24), False),            # Saturday
])
def test_business_hours(when, expected):
    cfg = {"timezone": "America/Chicago", "business_hours_start": 9, "business_hours_end": 19}
    assert retry_worker.within_business_hours(cfg, when) is expected


async def test_retry_worker_claims_only_due_contacts(pool, monkeypatch):
    monkeypatch.setattr(get_settings(), "telephony_provider", "twilio")
    monkeypatch.setattr(retry_worker, "within_business_hours", lambda *_: True)
    dialed = []

    async def fake_dial(contact_id, call_log_id, phone):
        dialed.append(phone)
        return "CA_retry"

    monkeypatch.setattr(telephony, "initiate_outbound_call", fake_dial)
    due = await db.upsert_lead(TEST_PHONE_PREFIX + "701", "Due", None, {})
    later = await db.upsert_lead(TEST_PHONE_PREFIX + "702", "Later", None, {})
    now = datetime.now(timezone.utc)
    await pool.execute("UPDATE contacts SET stage='no_answer_retry', next_follow_up_at=$2 WHERE id=$1", due["id"], now - timedelta(minutes=1))
    await pool.execute("UPDATE contacts SET stage='follow_up_requested', next_follow_up_at=$2 WHERE id=$1", later["id"], now + timedelta(hours=2))
    await pool.execute("UPDATE contacts SET next_follow_up_at = NOW() + INTERVAL '1 day' WHERE next_follow_up_at <= NOW() AND phone NOT LIKE $1", TEST_PHONE_PREFIX + "%")

    assert await retry_worker.run_once() == 1
    assert dialed == [TEST_PHONE_PREFIX + "701"]
    assert await pool.fetchval("SELECT stage::text FROM contacts WHERE id=$1", due["id"]) == "calling_in_progress"
    assert await pool.fetchval("SELECT stage::text FROM contacts WHERE id=$1", later["id"]) == "follow_up_requested"
    assert await retry_worker.run_once() == 0  # claimed rows are not redialled


async def test_retry_worker_idle_in_webrtc_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "telephony_provider", "webrtc_only")
    assert await retry_worker.run_once() == 0
