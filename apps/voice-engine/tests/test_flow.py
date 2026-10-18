"""Conversation flow: stage guards, transitions, `next` instructions and post-call ground truth."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from src import db
from src.config import get_settings
from src.pipeline import post_call, tools
from src.pipeline.flow import FlowState, Stage, answer_key, overview
from src.pipeline.tools import ToolRuntime, run_tool
from src.schemas import CallContext, PostCallExtraction

from .conftest import TEST_PHONE_PREFIX

CAL = get_settings().cal_api_base_url
QUESTIONS = ["What is your primary goal?", "What timeline are you working with?"]


@pytest.fixture
async def rt():
    c = await db.upsert_lead(TEST_PHONE_PREFIX + "801", "Dana", None, {})
    log_id = await db.start_outbound_call(c["id"])
    config = {**(await db.get_agent_config()), "qualification_questions": QUESTIONS}
    return ToolRuntime(call=CallContext(contact_id=c["id"], call_log_id=log_id, direction="outbound", transport="webrtc"),
                       config=config, contact=c)


def call(rt, name):
    return run_tool(rt, name, getattr(tools, name))


def slots_payload(n=4):
    base = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    return {"data": {"d": [{"start": (base + timedelta(hours=i)).isoformat()} for i in range(n)]}}


# --- pure state machine ----------------------------------------------------------


def test_answer_keys_are_short_and_stable():
    assert answer_key("What is your primary goal?") == "primary_goal"
    assert answer_key("What timeline are you working with?") == "timeline"
    assert answer_key("???") == "answer"


def test_flow_starts_in_qualify_and_points_at_first_question():
    f = FlowState(questions=QUESTIONS)
    assert f.stage == Stage.QUALIFY and f.path[0]["stage"] == "qualify"
    assert "What is your primary goal?" in f.step()["next"]


def test_terminal_stages_allow_nothing():
    f = FlowState(questions=QUESTIONS)
    f.enter(Stage.CLOSED)
    assert not any(f.allows(t) for t in ("check_calendar_availability", "end_call", "book_calendar_appointment"))


def test_overview_lists_questions_and_next_rule():
    text = overview(QUESTIONS)
    assert "What is your primary goal?" in text and '"next"' in text


# --- guarded tools -----------------------------------------------------------------


async def test_booking_is_rejected_before_scheduling(rt):
    out = await call(rt, "book_calendar_appointment")(slot_iso="2099-01-01T15:00:00+00:00",
                                                     attendee_name="Dana", attendee_email="d@example.com")
    assert out["success"] is False and out["stage"] == "qualify"
    assert "not available in the qualify step" in out["error"]
    assert "primary goal" in out["next"]


async def test_qualification_saves_answers_and_tracks_missing(rt):
    out = await call(rt, "save_qualification")(primary_goal="More inbound leads", ready_to_book=False, bogus="x")
    assert out["saved"] == ["primary_goal"] and rt.flow.stage == Stage.QUALIFY
    assert out["still_missing"] == ["What timeline are you working with?"]
    assert "timeline" in out["next"].lower()

    out = await call(rt, "save_qualification")(timeline="This quarter", ready_to_book=True)
    assert out["stage"] == "schedule" and rt.flow.answers == {"primary_goal": "More inbound leads", "timeline": "This quarter"}
    assert "Ask which day" in out["next"] and "Do not pick a day" in out["next"]


@respx.mock
async def test_happy_path_qualify_schedule_book_close(rt, pool, monkeypatch):
    respx.get(f"{CAL}/slots").mock(return_value=httpx.Response(200, json=slots_payload()))
    hung_up = asyncio.Event()

    async def hangup():
        hung_up.set()

    rt.hangup = hangup
    monkeypatch.setattr(tools, "HANGUP_DELAY_SECS", 0.01)

    await call(rt, "save_qualification")(primary_goal="Grow", timeline="ASAP", ready_to_book=True)
    avail = await call(rt, "check_calendar_availability")(date_str=(datetime.now() + timedelta(days=2)).date().isoformat())
    assert avail["stage"] == "schedule" and len(avail["slots"]) == 3
    assert "Offer the times" in avail["next"]

    slot = avail["slots"][0]["slot_iso"]
    respx.post(f"{CAL}/bookings").mock(return_value=httpx.Response(201, json={"data": {"uid": "bk_flow_1", "start": slot}}))
    booked = await call(rt, "book_calendar_appointment")(slot_iso=slot, attendee_name="Dana Reyes", attendee_email="dana@example.com")
    assert booked["success"] and booked["stage"] == "booked" and "end_call" in booked["next"]

    closed = await call(rt, "end_call")(reason="completed")
    assert closed["stage"] == "closed"
    await asyncio.wait_for(hung_up.wait(), 1)
    assert [p["stage"] for p in rt.flow.path] == ["qualify", "schedule", "booked", "closed"]
    assert await pool.fetchval("SELECT stage::text FROM contacts WHERE id=$1", rt.call.contact_id) == "appointment_booked"


@respx.mock
async def test_unoffered_slot_cannot_be_booked(rt):
    respx.get(f"{CAL}/slots").mock(return_value=httpx.Response(200, json=slots_payload()))
    await call(rt, "check_calendar_availability")(date_str=(datetime.now() + timedelta(days=2)).date().isoformat())
    out = await call(rt, "book_calendar_appointment")(slot_iso="2099-05-05T09:00:00+00:00",
                                                     attendee_name="Dana", attendee_email="d@example.com")
    assert out["success"] is False and "returned on this call" in out["error"] and not rt.booked


async def test_callback_requires_future_offset_time(rt):
    bad = await call(rt, "schedule_callback")(callback_iso="tomorrow at 3")
    naive = await call(rt, "schedule_callback")(callback_iso="2099-01-06T15:00:00")
    assert bad["success"] is False and naive["success"] is False and rt.flow.stage == Stage.QUALIFY

    ok = await call(rt, "schedule_callback")(callback_iso="2099-01-06T15:00:00-06:00", reason="driving")
    assert ok["success"] and ok["stage"] == "callback" and rt.flow.callback_at.isoformat() == "2099-01-06T15:00:00-06:00"


@respx.mock
async def test_changing_mind_after_callback_clears_it(rt):
    respx.get(f"{CAL}/slots").mock(return_value=httpx.Response(200, json=slots_payload()))
    await call(rt, "schedule_callback")(callback_iso="2099-01-06T15:00:00-06:00")
    out = await call(rt, "check_calendar_availability")(date_str="2099-01-05")
    assert out["stage"] == "schedule" and rt.flow.callback_at is None


async def test_unknown_end_reason_defaults_to_completed(rt):
    await call(rt, "end_call")(reason="bored")
    assert rt.flow.end_reason == "completed"


# --- post-call ground truth --------------------------------------------------------


def neutral(outcome="booked"):
    return PostCallExtraction(outcome=outcome, sentiment="neutral", summary="s")


def test_apply_flow_callback_overrides_classifier():
    f = FlowState(questions=QUESTIONS)
    f.callback_at = datetime(2099, 1, 6, 21, 0, tzinfo=timezone.utc)
    f.answers = {"primary_goal": "Grow"}
    out = post_call.apply_flow(neutral("not_interested"), f)
    assert out.outcome == "follow_up_requested" and out.requested_callback_iso == "2099-01-06T21:00:00+00:00"
    assert out.extracted_custom_fields["primary_goal"] == "Grow"


@pytest.mark.parametrize("reason,expected", [("not_interested", "not_interested"), ("wrong_number", "not_interested"),
                                             ("voicemail", "voicemail"), ("completed", "booked")])
def test_apply_flow_end_reasons(reason, expected):
    f = FlowState(questions=QUESTIONS)
    f.end_reason = reason
    assert post_call.apply_flow(neutral("booked"), f).outcome == expected


async def test_run_post_call_stores_flow_and_answers(rt, pool, monkeypatch):
    async def fake(*a, **k):
        return neutral("follow_up_requested")

    monkeypatch.setattr(post_call.llm, "extract", fake)
    await call(rt, "save_qualification")(primary_goal="Grow", ready_to_book=False)
    await call(rt, "end_call")(reason="not_interested")
    transcript = [{"role": "assistant", "content": "Hi"}, {"role": "user", "content": "Not for me, thanks"}]
    contact = await post_call.run_post_call(rt.call, transcript, started_at=datetime.now(timezone.utc), duration_seconds=12,
                                            booked=False, transferred=False, tz="UTC", flow=rt.flow)
    assert contact["stage"] == "not_interested" and contact["custom_fields"]["primary_goal"] == "Grow"
    data = await pool.fetchval("SELECT extracted_data FROM call_logs WHERE id=$1", rt.call.call_log_id)
    assert [p["stage"] for p in data["flow"]["path"]] == ["qualify", "closed"] and data["flow"]["end_reason"] == "not_interested"


@respx.mock
async def test_incomplete_email_is_rejected_before_booking(rt):
    respx.get(f"{CAL}/slots").mock(return_value=httpx.Response(200, json=slots_payload()))
    avail = await call(rt, "check_calendar_availability")(date_str=(datetime.now() + timedelta(days=2)).date().isoformat())
    booking = respx.post(f"{CAL}/bookings")
    out = await call(rt, "book_calendar_appointment")(slot_iso=avail["slots"][0]["slot_iso"],
                                                     attendee_name="Dana Reyes", attendee_email="danaexample.com")
    assert out["success"] is False and "spell it" in out["error"] and out["stage"] == "schedule"
    assert not booking.called and not rt.booked
