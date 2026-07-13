"""
Simple in-memory rate limiter for API endpoints.

Uses a sliding-window approach: tracks timestamps per key (IP) and
rejects requests when the count within the window exceeds the limit.

No external dependencies — avoids adding slowapi/redis for P3 polish.
"""

import time
from collections import defaultdict
from typing import Callable

from fastapi import HTTPException, Request, status


class RateLimiter:
    """
    Sliding-window rate limiter keyed by client IP.

    Usage as FastAPI dependency::

        limiter = RateLimiter(max_requests=10, window_seconds=60)
        @app.post("/login")
        async def login(request: Request, _: None = Depends(limiter)):
            ...
    """

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._store: dict[str, list[float]] = defaultdict(list)

    def _prune(self, key: str, now: float) -> None:
        """Remove timestamps outside the current window."""
        cutoff = now - self._window
        timestamps = self._store[key]
        # Keep only recent entries (list is typically small)
        self._store[key] = [t for t in timestamps if t > cutoff]

    def _is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        self._prune(key, now)
        timestamps = self._store[key]
        if len(timestamps) >= self._max:
            return False
        timestamps.append(now)
        return True

    async def __call__(self, request: Request) -> None:
        # Use X-Forwarded-For if behind a proxy, else client host
        forwarded = request.headers.get("X-Forwarded-For")
        client_ip = forwarded.split(",")[0].strip() if forwarded else (
            request.client.host if request.client else "unknown"
        )
        key = f"{request.url.path}:{client_ip}"

        if not self._is_allowed(key):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="请求过于频繁，请稍后重试",
                headers={"Retry-After": str(int(self._window))},
            )


# ── Pre-configured limiters for auth endpoints (T127) ───────────────

register_limiter = RateLimiter(max_requests=5, window_seconds=60)   # 5/min
login_limiter = RateLimiter(max_requests=10, window_seconds=60)     # 10/min
