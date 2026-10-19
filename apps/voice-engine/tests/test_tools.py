from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from src import db
from src.config import get_settings
from src.pipeline import tools
from src.pipeline.tools import ToolRuntime
from src.schemas import CallContext

from .conftest import TEST_PHONE_PREFIX

CAL = get_settings().cal_api_base_url


@pytest.fixture
async def rt():
    c = await db.upsert_lead(TEST_PHONE_PREFIX + "401", "Jane", None, {})
    log_id = await db.start_outbound_call(c["id"])
    config = await db.get_agent_config()
    return ToolRuntime(call=CallContext(contact_id=c["id"], call_log_id=log_id, direction="outbound", transport="webrtc"),
                       config=config, contact=c)


def offer(rt, slot_iso):
    """Simulate check_calendar_availability having offered this slot on the call."""
    rt.offered_slots[slot_iso] = datetime.fromisoformat(slot_iso)
    rt.flow.enter(tools.Stage.SCHEDULE)


def future_slots(n):
    base = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    return [{"start": (base + timedelta(minutes=30 * i)).isoformat()} for i in range(n)]


@respx.mock
async def test_availability_returns_three_spread_slots(rt):
    route = respx.get(f"{CAL}/slots").mock(return_value=httpx.Response(200, json={"status": "success", "data": {"d": future_slots(8)}}))
    out = await tools.check_calendar_availability(rt, (datetime.now() + timedelta(days=2)).date().isoformat())
    assert len(out["slots"]) == 3
    assert route.calls[0].request.headers["cal-api-version"] == "2024-09-04"
    assert route.calls[0].request.url.params["eventTypeId"] == str(rt.config["cal_event_type_id"])
    assert out["slots"][0]["spoken"]


async def test_availability_rejects_bad_date(rt):
    assert "error" in await tools.check_calendar_availability(rt, "next tuesday")


@respx.mock
async def test_booking_success_is_atomic(rt, pool):
    slot = future_slots(1)[0]["start"]
    offer(rt, slot)
    route = respx.post(f"{CAL}/bookings").mock(return_value=httpx.Response(201, json={"status": "success", "data": {"uid": "bk_123", "start": slot}}))
    out = await tools.book_calendar_appointment(rt, slot, "Jane Doe", "jane@example.com", "wants a demo")
    assert out["success"] is True and rt.booked
    assert route.calls[0].request.headers["cal-api-version"] == "2024-08-13"
    contact = await pool.fetchrow("SELECT * FROM contacts WHERE id = $1", rt.call.contact_id)
    assert (contact["stage"], contact["full_name"], contact["email"]) == ("appointment_booked", "Jane Doe", "jane@example.com")
    appt = await pool.fetchrow("SELECT * FROM appointments WHERE cal_booking_uid = 'bk_123'")
    assert appt["call_log_id"] == rt.call.call_log_id and appt["contact_id"] == rt.call.contact_id


@respx.mock
async def test_booking_failure_writes_nothing(rt, pool):
    respx.post(f"{CAL}/bookings").mock(return_value=httpx.Response(400, json={"error": "slot taken"}))
    slot = future_slots(1)[0]["start"]
    offer(rt, slot)
    out = await tools.book_calendar_appointment(rt, slot, "Jane", "jane@example.com")
    assert out["success"] is False and not rt.booked
    assert await pool.fetchval("SELECT stage::text FROM contacts WHERE id = $1", rt.call.contact_id) == "calling_in_progress"
    assert await pool.fetchval("SELECT count(*) FROM appointments WHERE contact_id = $1", rt.call.contact_id) == 0


@respx.mock
async def test_db_failure_rolls_back_both_writes(rt, pool, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upcoming_appointments_per_contact", 5)
    slot = future_slots(1)[0]["start"]
    offer(rt, slot)
    respx.post(f"{CAL}/bookings").mock(return_value=httpx.Response(201, json={"data": {"uid": "bk_dup", "start": slot}}))
    await tools.book_calendar_appointment(rt, slot, "Jane", "jane@example.com")
    await db.set_stage(rt.call.contact_id, "calling_in_progress")
    # Same booking uid again violates the UNIQUE constraint → the contact update must roll back too.
    with pytest.raises(Exception):
        await tools.book_calendar_appointment(rt, slot, "Someone Else", "else@example.com")
    row = await pool.fetchrow("SELECT stage::text, full_name FROM contacts WHERE id = $1", rt.call.contact_id)
    assert tuple(row) == ("calling_in_progress", "Jane")


async def test_transfer_without_number(rt):
    rt.config["human_transfer_phone"] = None
    out = await tools.transfer_to_human(rt, "wants a person")
    assert out["success"] is False and not rt.transferred


async def test_transfer_webrtc_simulated(rt, pool):
    rt.config["human_transfer_phone"] = "+15550109999"
    out = await tools.transfer_to_human(rt, "wants a person")
    assert out["success"] and rt.transferred
    assert await pool.fetchval("SELECT stage::text FROM contacts WHERE id = $1", rt.call.contact_id) == "transferred_to_human"


def test_tool_schemas_registered(rt):
    names = [t.name for t in tools.build_tools(rt)]
    assert names == ["save_qualification", "check_calendar_availability", "book_calendar_appointment",
                     "schedule_callback", "transfer_to_human", "end_call"]
