from datetime import datetime
from zoneinfo import ZoneInfo

from src.pipeline import prompts

CONFIG = {
    "agent_name": "Alex", "company_name": "Apex Services", "timezone": "America/Chicago",
    "outbound_greeting": "Hi {{full_name}}, this is {{agent_name}} calling from {{company_name}}.",
    "inbound_greeting": "Thanks for calling {{company_name}}, this is {{agent_name}}.",
    "qualification_questions": ["What is your primary goal?"],
    "objection_handlers": {"too_expensive": "We offer flexible tiers."},
}
NOW = datetime(2026, 10, 19, 10, 30, tzinfo=ZoneInfo("America/Chicago"))


def test_render_template_fills_and_falls_back():
    assert prompts.render_template("Hi {{full_name}}, I'm {{ agent_name }}.", {"agent_name": "Alex"}) == "Hi there, I'm Alex."


def test_outbound_instruction_uses_first_name_and_greeting():
    text = prompts.compile_system_instruction(
        config=CONFIG, contact={"full_name": "Jane Doe", "phone": "+15550100000"},
        direction="outbound", upcoming_appointments=[], now=NOW)
    assert '"Hi Jane, this is Alex calling from Apex Services."' in text
    assert "Monday, October 19, 2026" in text
    assert "What is your primary goal?" in text
    assert "too expensive: We offer flexible tiers." in text
    assert "filler" in text


def test_inbound_instruction_mentions_existing_booking():
    appt = {"scheduled_at": datetime(2026, 10, 21, 20, 0, tzinfo=ZoneInfo("UTC"))}
    text = prompts.compile_system_instruction(
        config=CONFIG, contact={"phone": "+15550100000"}, direction="inbound",
        upcoming_appointments=[appt], now=NOW)
    assert "Thanks for calling Apex Services, this is Alex." in text
    assert "already has a confirmed booking on Wednesday, October 21 at 3:00 PM" in text
