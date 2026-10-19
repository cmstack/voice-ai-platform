"""In-process rate limiting (single-tenant, single process; no extra infrastructure).

Keys on the client IP as seen by uvicorn. Behind a reverse proxy, run uvicorn with
`--proxy-headers --forwarded-allow-ips=<proxy ip>` so the real client IP is used; never trust
X-Forwarded-For from arbitrary clients.
"""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from src.config import get_settings

WINDOW_SECS = 60.0


class SlidingWindowLimiter:
    def __init__(self):
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window: float = WINDOW_SECS, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        hits = self._hits[key]
        while hits and now - hits[0] >= window:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


limiter = SlidingWindowLimiter()

_LIMITS = {
    "leads": lambda: get_settings().leads_per_minute,
    "webrtc": lambda: get_settings().webrtc_offers_per_minute,
    "webrtc_ice": lambda: get_settings().webrtc_offers_per_minute * 20,  # ICE trickles several patches per call
}


def limit(name: str):
    """FastAPI dependency: 429 once a client exceeds the per-minute limit for `name`."""

    async def dependency(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(f"{name}:{client}", _LIMITS[name]()):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded", headers={"Retry-After": "60"}
            )

    return dependency


class ConcurrencyGate:
    """Caps simultaneous browser calls (each one is a paid Bedrock session)."""

    def __init__(self):
        self.active = 0

    def try_acquire(self) -> bool:
        if self.active >= get_settings().max_concurrent_browser_calls:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        self.active = max(0, self.active - 1)


browser_calls = ConcurrencyGate()
