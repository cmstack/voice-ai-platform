"""Request guards: Supabase session JWTs for the dashboard, a shared secret for lead webhooks."""

import hmac
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException, status

from src.config import get_settings


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    base = get_settings().supabase_url.rstrip("/")
    return jwt.PyJWKClient(f"{base}/auth/v1/.well-known/jwks.json", cache_keys=True)


def verify_supabase_jwt(token: str) -> dict:
    """Supabase signs with HS256 (legacy shared secret) or asymmetric keys published as JWKS."""
    try:
        alg = jwt.get_unverified_header(token).get("alg", "HS256")
        if alg == "HS256":
            key = get_settings().supabase_jwt_secret
        else:
            key = _jwks_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=[alg], audience="authenticated")
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}") from exc
    if claims.get("role") != "authenticated":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "authenticated role required")
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
    secret = get_settings().lead_webhook_secret
    if x_webhook_secret and secret and hmac.compare_digest(x_webhook_secret, secret):
        return {"role": "webhook"}
    token = _bearer(authorization)
    if token:
        return verify_supabase_jwt(token)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer token or X-Webhook-Secret required")
