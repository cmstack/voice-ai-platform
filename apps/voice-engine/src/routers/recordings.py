"""Admin-only recording playback. Recordings are fetched from Twilio with the account credentials,
so the dashboard never handles raw Twilio media URLs (public-by-link unless HTTP auth is enforced)."""

from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from src import db
from src.config import get_settings
from src.services.auth import require_user

router = APIRouter(prefix="/api/recordings", tags=["recordings"])

TWILIO_MEDIA_HOST = "api.twilio.com"


def is_twilio_recording_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == TWILIO_MEDIA_HOST and "/Recordings/" in parsed.path


@router.get("/{call_log_id}")
async def get_recording(call_log_id: UUID, _user: dict = Depends(require_user)) -> Response:
    log = await db.get_call_log(call_log_id)
    if not log or not log["recording_url"]:
        raise HTTPException(404, "no recording for this call")
    url = log["recording_url"]
    if not is_twilio_recording_url(url):  # never let a stored URL turn this into an open proxy (SSRF)
        raise HTTPException(404, "no recording for this call")
    s = get_settings()
    if not (s.twilio_account_sid and s.twilio_auth_token):
        raise HTTPException(503, "Twilio credentials are not configured")
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r = await client.get(url, auth=(s.twilio_account_sid, s.twilio_auth_token))
    if r.status_code != 200:
        raise HTTPException(502, "recording unavailable")
    return Response(
        content=r.content,
        media_type=r.headers.get("content-type", "audio/mpeg"),
        headers={"Cache-Control": "private, no-store"},
    )
