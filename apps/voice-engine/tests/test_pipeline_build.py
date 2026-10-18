"""Construction smoke test: catches Pipecat API drift without needing AWS or a live call."""

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair

from src import db
from src.pipeline.tools import ToolRuntime
from src.pipeline.voice_pipeline import build_llm
from src.schemas import CallContext

from .conftest import TEST_PHONE_PREFIX


async def test_nova_sonic_pipeline_builds(monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "aws_access_key_id", "AKIATEST")
    monkeypatch.setattr(get_settings(), "aws_secret_access_key", "secret")
    c = await db.upsert_lead(TEST_PHONE_PREFIX + "501", "Pat", None, {})
    log_id = await db.start_outbound_call(c["id"])
    rt = ToolRuntime(call=CallContext(contact_id=c["id"], call_log_id=log_id, direction="inbound", transport="webrtc"),
                     config=await db.get_agent_config(), contact=c)
    llm = build_llm("You are a test agent.", "tiffany", rt)
    assert llm._settings.model == "amazon.nova-2-5-sonic"
    assert llm._settings.voice == "tiffany"
    assert {t.name for t in llm._tools.standard_tools} == {
        "save_qualification", "check_calendar_availability", "book_calendar_appointment",
        "schedule_callback", "transfer_to_human", "end_call"}
    agg = LLMContextAggregatorPair(LLMContext())
    worker = PipelineWorker(Pipeline([agg.user(), llm, agg.assistant()]), enable_rtvi=True)
    assert worker is not None
