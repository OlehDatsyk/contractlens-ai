"""In-process sliding-window rate limiting.

Limits are per client IP (and optionally per account identifier). They apply per API process; for multi-instance
deployments put an edge rate limiter (e.g. Cloudflare / load balancer) in front as well - see SECURITY.md.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


class SlidingWindow:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def hit(self, key: str, limit: int, window: float) -> float | None:
        """Record a hit; return seconds to wait if the limit is exceeded."""
        now = time.monotonic()
        with self._lock:
            if now - self._last_sweep > 300:
                for k in [k for k, q in self._hits.items() if not q or now - q[-1] > 3600]:
                    del self._hits[k]
                self._last_sweep = now
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return window - (now - q[0])
            q.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindow()


def client_ip(request: Request) -> str:
    if get_settings().trust_proxy_headers:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            # The right-most entry is the one appended by the trusted proxy (left-most values are client-controlled).
            return fwd.split(",")[-1].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def rate_limit(bucket: str, limit: int, window_seconds: int) -> Callable[[Request], None]:
    def dependency(request: Request) -> None:
        if not get_settings().rate_limit_enabled:
            return
        wait = limiter.hit(f"{bucket}:{client_ip(request)}", limit, window_seconds)
        if wait is not None:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Please try again later.",
                                headers={"Retry-After": str(int(wait) + 1)})

    return dependency


def check_identifier(bucket: str, identifier: str, limit: int, window_seconds: int) -> None:
    if not get_settings().rate_limit_enabled:
        return
    if limiter.hit(f"{bucket}:id:{identifier.lower()}", limit, window_seconds) is not None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Please try again later.")
