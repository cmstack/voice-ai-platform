"""Request guards: admin Supabase session JWTs for the dashboard, a shared secret for lead webhooks."""

import hmac
import time
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Header, HTTPException, status
from loguru import logger

from src import db
from src.config import get_settings

ROLE_CACHE_SECS = 30.0
_role_cache: dict[UUID, tuple[float, str | None]] = {}


async def current_role(user_id: UUID) -> str | None:
    """Role from the database, so revoking admin works within ROLE_CACHE_SECS, not at JWT expiry."""
    now = time.monotonic()
    cached = _role_cache.get(user_id)
    if cached and now - cached[0] < ROLE_CACHE_SECS:
        return cached[1]
    role = await db.user_role(user_id)
    _role_cache[user_id] = (now, role)
    return role


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    base = get_settings().supabase_url.rstrip("/")
    return jwt.PyJWKClient(f"{base}/auth/v1/.well-known/jwks.json", cache_keys=True)


def verify_supabase_jwt_claims(token: str) -> dict:
    """Supabase signs with HS256 (legacy shared secret) or asymmetric keys published as JWKS.

    Being signed in is not enough: the token must carry app_metadata.role == ADMIN_ROLE, the same
    rule `public.is_admin()` enforces in the database. app_metadata is server-controlled.
    """
    try:
        alg = jwt.get_unverified_header(token).get("alg", "HS256")
        if alg == "HS256":
            key = get_settings().supabase_jwt_secret
        else:
            key = _jwks_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=[alg], audience="authenticated")
    except jwt.PyJWTError as exc:
        logger.info(f"Rejected JWT: {exc}")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token") from exc
    if claims.get("role") != "authenticated":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not allowed")
    if (claims.get("app_metadata") or {}).get("role") != get_settings().admin_role:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin access required")
    return claims


async def verify_supabase_jwt(token: str) -> dict:
    """Signature + claims, then confirm the user is STILL an admin in auth.users."""
    claims = verify_supabase_jwt_claims(token)
    try:
        user_id = UUID(str(claims.get("sub")))
    except ValueError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin access required") from None
    if await current_role(user_id) != get_settings().admin_role:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin access required")
    return claims


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def require_user(authorization: str | None = Header(default=None)) -> dict:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    return await verify_supabase_jwt(token)


async def require_user_or_webhook_secret(
    authorization: str | None = Header(default=None),
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    settings = get_settings()
    if x_webhook_secret and settings.lead_webhook_enabled() and hmac.compare_digest(
        x_webhook_secret.encode(), settings.lead_webhook_secret.encode()
    ):
        return {"role": "webhook", "sub": "lead-webhook"}
    token = _bearer(authorization)
    if token:
        return await verify_supabase_jwt(token)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer token or X-Webhook-Secret required")
