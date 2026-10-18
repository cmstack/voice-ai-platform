"""APScheduler job that redials contacts whose next_follow_up_at has come due."""

from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from src import db
from src.services import telephony

POLL_SECONDS = 60
RETRY_ON_ERROR_MINUTES = 30


def within_business_hours(config: dict, now: datetime | None = None) -> bool:
    local = (now or datetime.now(ZoneInfo("UTC"))).astimezone(ZoneInfo(config["timezone"]))
    if local.weekday() >= 5:  # no weekend redials
        return False
    return config["business_hours_start"] <= local.hour < config["business_hours_end"]


async def run_once(now: datetime | None = None) -> int:
    if not telephony.telephony_enabled():
        return 0
    config = await db.get_agent_config()
    if not within_business_hours(config, now):
        return 0
    claimed = await db.claim_due_follow_ups()
    for contact, call_log_id in claimed:
        try:
            await telephony.initiate_outbound_call(contact["id"], call_log_id, contact["phone"])
        except Exception:
            logger.exception(f"Redial failed for {contact['phone']}")
            await db.reschedule(contact["id"], contact["stage"], RETRY_ON_ERROR_MINUTES)
    return len(claimed)


def start() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_once, "interval", seconds=POLL_SECONDS, max_instances=1, coalesce=True)
    scheduler.start()
    return scheduler
