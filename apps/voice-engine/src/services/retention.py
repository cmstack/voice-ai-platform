"""Data retention: purge call content older than RETENTION_DAYS.

Clears transcripts, recordings (also deleted at Twilio when telephony is on) and extracted details
(qualification answers, flow path). Outcome, sentiment, duration and the short summary stay so the
CRM history remains useful; contacts themselves are kept.
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from src import db
from src.config import get_settings
from src.services import telephony

_RECORDING_SID = re.compile(r"/Recordings/(RE[0-9a-fA-F]{32})")


async def _delete_twilio_recording(url: str) -> None:
    match = _RECORDING_SID.search(url or "")
    if not (match and telephony.telephony_enabled()):
        return
    try:
        await asyncio.to_thread(lambda: telephony._client().recordings(match.group(1)).delete())
    except Exception:
        logger.exception("Could not delete an expired recording at Twilio")


async def purge_once(now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=get_settings().retention_days)
    purged = await db.purge_call_content(cutoff)
    for _call_log_id, recording_url in purged:
        await _delete_twilio_recording(recording_url)
    if purged:
        logger.info(f"Retention: purged content of {len(purged)} call(s) older than {cutoff:%Y-%m-%d}")
    return len(purged)


def schedule(scheduler: AsyncIOScheduler) -> None:
    scheduler.add_job(purge_once, "interval", hours=24, next_run_time=datetime.now(timezone.utc),
                      max_instances=1, coalesce=True, id="retention")
