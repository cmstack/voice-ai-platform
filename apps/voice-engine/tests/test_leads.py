import pytest

from src import db
from src.routers import leads
from src.services import telephony

from .conftest import TEST_PHONE_PREFIX, make_client

PHONE = TEST_PHONE_PREFIX + "199"


async def test_lead_requires_auth():
    async with make_client(leads.router) as c:
        r = await c.post("/api/leads", json={"phone": PHONE})
    assert r.status_code == 401


async def test_create_lead_upserts_new_lead(auth_headers, pool):
    async with make_client(leads.router) as c:
        r1 = await c.post("/api/leads", headers=auth_headers, json={
            "phone": PHONE, "full_name": "Jane Doe", "email": "jane@example.com",
            "custom_fields": {"interest": "Consultation"}})
        assert r1.status_code == 201, r1.text
        assert r1.json()["stage"] == "new_lead"

        # simulate the card having moved on, then a second form submission
        await db.set_stage(r1.json()["contact_id"], "not_interested")
        r2 = await c.post("/api/leads", headers=auth_headers, json={
            "phone": PHONE, "custom_fields": {"budget": "5k"}})
    assert r2.json()["contact_id"] == r1.json()["contact_id"]
    row = await pool.fetchrow("SELECT * FROM contacts WHERE phone = $1", PHONE)
    assert row["stage"] == "new_lead"
    assert row["full_name"] == "Jane Doe"  # not clobbered by the null in the 2nd payload
    assert row["custom_fields"] == {"interest": "Consultation", "budget": "5k"}


async def test_phone_is_normalised(auth_headers, pool):
    async with make_client(leads.router) as c:
        r = await c.post("/api/leads", headers=auth_headers, json={"phone": "(555) 010-1234"})
    assert r.status_code == 201
    assert await pool.fetchval("SELECT phone FROM contacts WHERE id = $1", r.json()["contact_id"]) == "+15550101234"


async def test_trigger_call_creates_pending_call_log(auth_headers, pool, monkeypatch):
    dialed = {}

    async def fake_dial(contact_id, call_log_id, phone):
        dialed.update(contact_id=contact_id, call_log_id=call_log_id, phone=phone)
        return "CA_test"

    monkeypatch.setattr(telephony, "initiate_outbound_call", fake_dial)
    async with make_client(leads.router) as c:
        r = await c.post("/api/leads", headers=auth_headers,
                         json={"phone": PHONE, "trigger_call_immediately": True})
    body = r.json()
    assert body["stage"] == "calling_in_progress" and body["dialed"] is True
    assert dialed["phone"] == PHONE and str(dialed["call_log_id"]) == body["call_log_id"]
    log = await pool.fetchrow("SELECT * FROM call_logs WHERE id = $1", body["call_log_id"])
    assert log["direction"] == "outbound" and log["outcome"] is None
    assert await pool.fetchval("SELECT stage::text FROM contacts WHERE id = $1", body["contact_id"]) == "calling_in_progress"


async def test_failed_dial_falls_back_to_retry(auth_headers, pool, monkeypatch):
    async def boom(*_):
        raise RuntimeError("twilio down")

    monkeypatch.setattr(telephony, "initiate_outbound_call", boom)
    async with make_client(leads.router) as c:
        r = await c.post("/api/leads", headers=auth_headers,
                         json={"phone": PHONE, "trigger_call_immediately": True})
    assert r.json()["stage"] == "no_answer_retry"


async def test_webhook_secret_auth(monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "lead_webhook_secret", "s3cret")
    async with make_client(leads.router) as c:
        ok = await c.post("/api/leads", headers={"X-Webhook-Secret": "s3cret"}, json={"phone": PHONE})
        bad = await c.post("/api/leads", headers={"X-Webhook-Secret": "nope"}, json={"phone": PHONE})
    assert ok.status_code == 201 and bad.status_code == 401
