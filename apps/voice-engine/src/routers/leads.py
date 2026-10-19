from fastapi import APIRouter, Depends
from loguru import logger

from src import db
from src.schemas import LeadIn, LeadOut
from src.services import dial_guard, telephony
from src.services.auth import require_user_or_webhook_secret
from src.services.rate_limit import limit

router = APIRouter(prefix="/api/leads", tags=["leads"])


@router.post("", response_model=LeadOut, status_code=201, dependencies=[Depends(limit("leads"))])
async def create_lead(
    lead: LeadIn, caller: dict = Depends(require_user_or_webhook_secret)
) -> LeadOut:
    consent_source = None
    if lead.consent:
        via = "dashboard" if caller.get("role") != "webhook" else "lead webhook"
        consent_source = f"{via}: {lead.consent_source or 'consent flag set'}"[:200]
    contact = await db.upsert_lead(lead.phone, lead.full_name, lead.email, lead.custom_fields, consent_source)
    if not lead.trigger_call_immediately:
        return LeadOut(contact_id=contact["id"], stage=contact["stage"])

    # Check dialing policy before creating a call row, so blocked leads don't get phantom calls.
    if telephony.telephony_enabled():
        try:
            await dial_guard.check(contact, await db.get_agent_config())
        except dial_guard.DialBlocked as blocked:
            if blocked.retry_at:
                await db.queue_dial(contact["id"], blocked.retry_at)
            logger.info(f"Lead {contact['id']} not dialed: {blocked.reason}")
            return LeadOut(contact_id=contact["id"], stage=contact["stage"],
                           blocked_reason=blocked.reason, next_attempt_at=blocked.retry_at)

    call_log_id = await db.start_outbound_call(contact["id"])
    dialed = False
    try:
        dialed = await telephony.initiate_outbound_call(contact["id"], call_log_id, lead.phone) is not None
    except dial_guard.DialBlocked as blocked:  # raced with another dial
        await db.delete_call_log(call_log_id)
        await db.set_stage(contact["id"], "new_lead")
        if blocked.retry_at:
            await db.queue_dial(contact["id"], blocked.retry_at)
        return LeadOut(contact_id=contact["id"], stage="new_lead",
                       blocked_reason=blocked.reason, next_attempt_at=blocked.retry_at)
    except Exception:
        # A failed dial is treated like an unanswered call so the retry worker picks it up.
        logger.exception(f"Outbound dial failed for {telephony.mask_phone(lead.phone)}")
        await db.set_stage(contact["id"], "no_answer_retry")
        return LeadOut(contact_id=contact["id"], stage="no_answer_retry", call_log_id=call_log_id)
    return LeadOut(
        contact_id=contact["id"], stage="calling_in_progress", call_log_id=call_log_id, dialed=dialed
    )
