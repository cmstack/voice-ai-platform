"""The one voice loop shared by inbound, outbound and browser (WebRTC) calls.

    transport.input() → user aggregator → Nova 2 Sonic (speech-to-speech + tools)
      → transport.output() → assistant aggregator

Nova Sonic opens its Bedrock session when it receives the first context. Inbound calls send that
context (ending in a user cue, which makes the agent speak first) as soon as the client connects.
Outbound calls hold it until the callee finishes their "Hello?" or 1.2 s of silence passes.
"""

import asyncio
import time
from datetime import datetime, timezone

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.aws.nova_sonic.llm import AWSNovaSonicLLMService
from pipecat.transports.base_transport import BaseTransport

from src import db
from src.config import get_settings
from src.pipeline import post_call, prompts
from src.pipeline.tools import ToolRuntime, build_tools
from src.schemas import CallContext

OUTBOUND_SILENCE_TIMEOUT_SECS = 1.2


def build_llm(system_instruction: str, voice: str, rt: ToolRuntime) -> AWSNovaSonicLLMService:
    s = get_settings()
    return AWSNovaSonicLLMService(
        access_key_id=s.aws_access_key_id,
        secret_access_key=s.aws_secret_access_key,
        session_token=s.aws_session_token or None,
        region=s.aws_region,
        settings=AWSNovaSonicLLMService.Settings(
            model=s.nova_sonic_model_id,
            voice=voice,
            system_instruction=system_instruction,
            endpointing_sensitivity="MEDIUM",
        ),
        tools=build_tools(rt),
    )


class VoiceSession:
    """Owns one call's pipeline, first-turn logic, transcript capture and post-call hand-off."""

    def __init__(self, transport: BaseTransport, call: CallContext, *, enable_rtvi: bool):
        self.transport = transport
        self.call = call
        self.enable_rtvi = enable_rtvi
        self.transcript: list[dict] = []
        self.started_at = datetime.now(timezone.utc)
        self._connected_at: float | None = None
        self._first_turn_sent = False
        self._user_speaking = False
        self._silence_timer: asyncio.Task | None = None

    async def run(self) -> None:
        config = await db.get_agent_config()
        contact = await db.get_contact(self.call.contact_id)
        if contact is None:
            raise ValueError(f"unknown contact {self.call.contact_id}")
        upcoming = await db.upcoming_appointments(self.call.contact_id) if self.call.direction == "inbound" else []

        self.rt = ToolRuntime(call=self.call, config=config, contact=contact)
        system_instruction = prompts.compile_system_instruction(
            config=config, contact=contact, direction=self.call.direction, upcoming_appointments=upcoming
        )
        llm = build_llm(system_instruction, config["nova_voice_id"], self.rt)
        self.context = LLMContext()
        aggregators = LLMContextAggregatorPair(
            self.context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=SileroVADAnalyzer(params=VADParams(start_secs=0.2, stop_secs=0.4))
            ),
        )
        pipeline = Pipeline([
            self.transport.input(),
            aggregators.user(),
            llm,
            self.transport.output(),
            aggregators.assistant(),
        ])
        self.worker = PipelineWorker(
            pipeline,
            params=PipelineParams(enable_metrics=True),
            enable_rtvi=self.enable_rtvi,
            idle_timeout_secs=120,
        )
        self._wire_events(aggregators)

        try:
            await PipelineRunner(handle_sigint=False).run(self.worker)
        finally:
            if self._silence_timer:
                self._silence_timer.cancel()
            await self._finish(config["timezone"])

    def _wire_events(self, aggregators: LLMContextAggregatorPair) -> None:
        @self.transport.event_handler("on_client_connected")
        async def on_connected(_transport, _client):
            self._connected_at = time.monotonic()
            logger.info(f"Call {self.call.call_log_id} connected ({self.call.direction}/{self.call.transport})")
            if self.call.direction == "inbound":
                await self._send_first_turn()
            else:
                self._silence_timer = asyncio.create_task(self._outbound_silence_timeout())

        @self.transport.event_handler("on_client_disconnected")
        async def on_disconnected(_transport, _client):
            logger.info(f"Call {self.call.call_log_id} disconnected")
            await self.worker.cancel(reason="client disconnected")

        user = aggregators.user()
        assistant = aggregators.assistant()

        @user.event_handler("on_user_turn_started")
        async def on_user_started(_agg, _strategy):
            self._user_speaking = True

        @user.event_handler("on_user_turn_stopped")
        async def on_user_stopped(_agg, _strategy, _message):
            self._user_speaking = False
            if self.call.direction == "outbound" and not self._first_turn_sent:
                # The callee said "Hello?" — now deliver the opener.
                await self._send_first_turn()

        @user.event_handler("on_user_turn_message_added")
        async def on_user_message(_agg, message):
            if message.content and message.content.strip():
                self.transcript.append({"role": "user", "content": message.content.strip(), "timestamp": message.timestamp})

        @assistant.event_handler("on_assistant_turn_stopped")
        async def on_assistant_message(_agg, message):
            if message.content and message.content.strip():
                self.transcript.append({
                    "role": "assistant", "content": message.content.strip(),
                    "timestamp": message.timestamp, "interrupted": message.interrupted,
                })

    async def _outbound_silence_timeout(self) -> None:
        await asyncio.sleep(OUTBOUND_SILENCE_TIMEOUT_SECS)
        if not self._user_speaking and not self._first_turn_sent:
            await self._send_first_turn()

    async def _send_first_turn(self) -> None:
        if self._first_turn_sent:
            return
        self._first_turn_sent = True
        self.context.add_message({"role": "user", "content": prompts.first_turn_seed(self.call.direction)})
        await self.worker.queue_frame(LLMRunFrame())

    async def _finish(self, tz: str) -> None:
        for task in list(self.rt.background):
            await asyncio.gather(task, return_exceptions=True)
        duration = int(time.monotonic() - self._connected_at) if self._connected_at else 0
        try:
            await post_call.run_post_call(
                self.call,
                self.transcript,
                started_at=self.started_at,
                duration_seconds=duration if self.call.transport == "webrtc" else None,
                booked=self.rt.booked,
                transferred=self.rt.transferred,
                tz=tz,
            )
        except Exception:
            logger.exception(f"Post-call processing failed for {self.call.call_log_id}")
