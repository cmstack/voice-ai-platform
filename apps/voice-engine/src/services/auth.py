"""Request guards: admin Supabase session JWTs for the dashboard, a shared secret for lead webhooks."""

import hmac
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException, status
from loguru import logger

from src.config import get_settings


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    base = get_settings().supabase_url.rstrip("/")
    return jwt.PyJWKClient(f"{base}/auth/v1/.well-known/jwks.json", cache_keys=True)


def verify_supabase_jwt(token: str) -> dict:
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


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def require_user(authorization: str | None = Header(default=None)) -> dict:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    return verify_supabase_jwt(token)


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
        return verify_supabase_jwt(token)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer token or X-Webhook-Secret required")
