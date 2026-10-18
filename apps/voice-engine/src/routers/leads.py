from fastapi import APIRouter, Depends
from loguru import logger

from src import db
from src.schemas import LeadIn, LeadOut
from src.services import telephony
from src.services.auth import require_user_or_webhook_secret

router = APIRouter(prefix="/api/leads", tags=["leads"])


@router.post("", response_model=LeadOut, status_code=201)
async def create_lead(lead: LeadIn, _caller: dict = Depends(require_user_or_webhook_secret)) -> LeadOut:
    contact = await db.upsert_lead(lead.phone, lead.full_name, lead.email, lead.custom_fields)
    if not lead.trigger_call_immediately:
        return LeadOut(contact_id=contact["id"], stage=contact["stage"])

    call_log_id = await db.start_outbound_call(contact["id"])
    dialed = False
    try:
        dialed = await telephony.initiate_outbound_call(contact["id"], call_log_id, lead.phone) is not None
    except Exception:
        # A failed dial is treated like an unanswered call so the retry worker picks it up.
        logger.exception(f"Outbound dial failed for {lead.phone}")
        await db.set_stage(contact["id"], "no_answer_retry")
        return LeadOut(contact_id=contact["id"], stage="no_answer_retry", call_log_id=call_log_id)
    return LeadOut(
        contact_id=contact["id"], stage="calling_in_progress", call_log_id=call_log_id, dialed=dialed
    )
