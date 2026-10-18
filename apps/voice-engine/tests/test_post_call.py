from datetime import datetime, timedelta, timezone

import pytest

from src.pipeline import post_call
from src.pipeline.post_call import decide_stage
from src.schemas import PostCallExtraction

NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)


def ex(outcome, callback=None):
    return PostCallExtraction(outcome=outcome, sentiment="neutral", summary="s", requested_callback_iso=callback)


def contact(stage="calling_in_progress", retries=0):
    return {"stage": stage, "retry_count": retries}


def test_voicemail_schedules_retry():
    assert decide_stage(contact(retries=1), ex("voicemail"), NOW) == {
        "stage": "no_answer_retry", "retry_count": 2, "next_follow_up_at": NOW + timedelta(hours=4)}


def test_voicemail_after_three_retries_gives_up():
    assert decide_stage(contact(retries=3), ex("voicemail"), NOW) == {"stage": "not_interested", "next_follow_up_at": None}


def test_follow_up_uses_requested_time():
    out = decide_stage(contact(), ex("follow_up_requested", "2026-10-20T15:00:00-05:00"), NOW)
    assert out["stage"] == "follow_up_requested"
    assert out["next_follow_up_at"] == datetime(2026, 10, 20, 20, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", [None, "not-a-date", "2020-01-01T00:00:00Z"])
def test_follow_up_defaults_to_24h(bad):
    assert decide_stage(contact(), ex("follow_up_requested", bad), NOW)["next_follow_up_at"] == NOW + timedelta(hours=24)


def test_not_interested_clears_follow_up():
    assert decide_stage(contact(), ex("not_interested"), NOW) == {"stage": "not_interested", "next_follow_up_at": None}


@pytest.mark.parametrize("stage", ["appointment_booked", "transferred_to_human"])
def test_terminal_stages_never_regress(stage):
    assert decide_stage(contact(stage=stage), ex("not_interested"), NOW) is None


@pytest.mark.parametrize("outcome", ["booked", "transferred"])
def test_tool_outcomes_leave_stage_to_tools(outcome):
    assert decide_stage(contact(), ex(outcome), NOW) is None


async def test_empty_transcript_is_voicemail_without_llm(monkeypatch):
    async def never(*a, **k):
        raise AssertionError("LLM should not be called")
    monkeypatch.setattr(post_call.llm, "extract", never)
    out = await post_call.analyse([{"role": "assistant", "content": "Hi!"}], started_at=NOW, tz="UTC", booked=False, transferred=False)
    assert out.outcome == "voicemail"


async def test_tool_ground_truth_overrides_classifier(monkeypatch):
    async def fake(*a, **k):
        return ex("not_interested")
    monkeypatch.setattr(post_call.llm, "extract", fake)
    out = await post_call.analyse([{"role": "user", "content": "ok book it"}], started_at=NOW, tz="UTC", booked=True, transferred=False)
    assert out.outcome == "booked"


async def test_run_post_call_persists_and_routes(pool, monkeypatch):
    from src import db
    from src.schemas import CallContext
    from .conftest import TEST_PHONE_PREFIX

    c = await db.upsert_lead(TEST_PHONE_PREFIX + "301", "Sam", None, {})
    log_id = await db.start_outbound_call(c["id"])

    async def fake(*a, **k):
        return PostCallExtraction(outcome="follow_up_requested", sentiment="positive", summary="Wants a call Tuesday.",
                                  requested_callback_iso="2099-01-06T15:00:00+00:00", extracted_custom_fields={"budget": "10k"})
    monkeypatch.setattr(post_call.llm, "extract", fake)
    call = CallContext(contact_id=c["id"], call_log_id=log_id, direction="outbound", transport="webrtc")
    transcript = [{"role": "assistant", "content": "Hi Sam"}, {"role": "user", "content": "Call me Tuesday"}]
    out = await post_call.run_post_call(call, transcript, started_at=NOW, duration_seconds=42, booked=False, transferred=False, tz="UTC")
    assert out["stage"] == "follow_up_requested"
    assert out["next_follow_up_at"] == datetime(2099, 1, 6, 15, 0, tzinfo=timezone.utc)
    assert out["custom_fields"]["budget"] == "10k"
    log = await pool.fetchrow("SELECT * FROM call_logs WHERE id = $1", log_id)
    assert (log["outcome"], log["sentiment"], log["duration_seconds"]) == ("follow_up_requested", "positive", 42)
    assert log["transcript"][1]["content"] == "Call me Tuesday"
