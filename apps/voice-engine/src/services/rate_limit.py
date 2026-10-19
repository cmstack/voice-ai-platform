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


MAX_TRACKED_KEYS = 10_000
PRUNE_EVERY = 1_000


class SlidingWindowLimiter:
    """Per-process and in-memory: it resets on restart and isn't shared between workers. Run a single
    uvicorn worker, or put a shared limiter (for example at the reverse proxy) in front."""

    def __init__(self):
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._calls = 0

    def _prune(self, now: float, window: float) -> None:
        for key in [k for k, h in self._hits.items() if not h or now - h[-1] >= window]:
            del self._hits[key]
        while len(self._hits) > MAX_TRACKED_KEYS:  # bound memory even under an IP-rotation flood
            self._hits.pop(next(iter(self._hits)))

    def allow(self, key: str, limit: int, window: float = WINDOW_SECS, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        self._calls += 1
        if self._calls % PRUNE_EVERY == 0 or len(self._hits) > MAX_TRACKED_KEYS:
            self._prune(now, window)
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
    """Caps simultaneous sessions (each live call is a paid Bedrock session, phone calls also cost Twilio)."""

    def __init__(self, limit_fn):
        self._limit = limit_fn
        self.active = 0

    def has_capacity(self) -> bool:
        return self.active < self._limit()

    def try_acquire(self) -> bool:
        if not self.has_capacity():
            return False
        self.active += 1
        return True

    def release(self) -> None:
        self.active = max(0, self.active - 1)


# Every live call (phone or browser) holds a slot in `all_calls`; browser calls also hold one in
# the smaller `browser_calls` pool.
all_calls = ConcurrencyGate(lambda: get_settings().max_concurrent_calls)
browser_calls = ConcurrencyGate(lambda: get_settings().max_concurrent_browser_calls)
