"""Tiny TTL dedupe set — Slack redelivers events, so we drop ones we've seen."""

from __future__ import annotations

import time


class TTLDedupe:
    def __init__(self, ttl: float = 300.0) -> None:
        self._ttl = ttl
        self._seen: dict[str, float] = {}

    def add(self, key: str) -> bool:
        """Return True if `key` is new (and record it), False if already seen."""
        now = time.monotonic()
        # opportunistic cleanup
        if len(self._seen) > 1000:
            self._seen = {k: v for k, v in self._seen.items() if now - v < self._ttl}
        last = self._seen.get(key)
        if last is not None and now - last < self._ttl:
            return False
        self._seen[key] = now
        return True
