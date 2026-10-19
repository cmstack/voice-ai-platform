"""APScheduler job that redials contacts whose next_follow_up_at has come due."""

from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from src import db
from src.services import dial_guard, telephony

POLL_SECONDS = 60
RETRY_ON_ERROR_MINUTES = 30


def within_business_hours(config: dict, now: datetime | None = None) -> bool:
    return dial_guard.within_business_hours(config, now)


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
        except dial_guard.DialBlocked as blocked:
            logger.info(f"Redial of {contact['id']} blocked: {blocked.reason}")
            await db.delete_call_log(call_log_id)
            await db.set_stage(contact["id"], contact["stage"])
            if blocked.retry_at:
                await db.queue_dial(contact["id"], blocked.retry_at)
        except Exception:
            logger.exception(f"Redial failed for {telephony.mask_phone(contact['phone'])}")
            await db.reschedule(contact["id"], contact["stage"], RETRY_ON_ERROR_MINUTES)
    return len(claimed)


def add_job(scheduler: AsyncIOScheduler) -> None:
    scheduler.add_job(run_once, "interval", seconds=POLL_SECONDS, max_instances=1, coalesce=True, id="redials")
