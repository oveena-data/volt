"""Per-user token-bucket rate limiting.

In-memory, per backend instance. With N instances the effective ceiling is
N x the configured rate — acceptable for the documented deployment sizes; the
shared inference semaphore + bounded queue are the hard backstop on model
load. (Documented in docs/OPERATIONS.md.)
"""

from __future__ import annotations

import time

from .config import settings
from .errors import ApiError

_buckets: dict[str, tuple[float, float]] = {}  # user_id -> (tokens, last_ts)


def check_turn_rate(user_id: str) -> None:
    rate = settings.rate_limit_turns_per_minute / 60.0
    burst = float(settings.rate_limit_burst)
    now = time.monotonic()
    tokens, last = _buckets.get(user_id, (burst, now))
    tokens = min(burst, tokens + (now - last) * rate)
    if tokens < 1.0:
        wait = int((1.0 - tokens) / rate) + 1
        raise ApiError(
            f"rate limit: try again in ~{wait}s", 429, code="rate_limited",
            extra={"retry_after_s": wait},
        )
    _buckets[user_id] = (tokens - 1.0, now)
    if len(_buckets) > 50000:  # bound memory
        _buckets.clear()


def reset_all() -> None:
    _buckets.clear()
