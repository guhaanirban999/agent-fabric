"""Policy-driven token-bucket rate limiter.

The *budget* comes from the OPA decision (`rate_limit` / `rate_window_seconds`), so
limits are governed by policy rather than hardcoded. In-memory per process; swap for
Redis when the gateways scale horizontally (noted in Phase 4 hardening).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    last: float
    capacity: float
    refill_per_sec: float


@dataclass
class TokenBucketLimiter:
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def allow(self, key: str, limit: int | None, window_seconds: int = 60) -> bool:
        """Return True if the call is within budget. `limit=None` => unlimited."""
        if limit is None or limit <= 0:
            return True
        now = time.monotonic()
        refill = limit / max(window_seconds, 1)
        bucket = self._buckets.get(key)
        if bucket is None:
            self._buckets[key] = _Bucket(
                tokens=limit - 1, last=now, capacity=limit, refill_per_sec=refill
            )
            return True
        elapsed = now - bucket.last
        bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.refill_per_sec)
        bucket.last = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return True
        return False
