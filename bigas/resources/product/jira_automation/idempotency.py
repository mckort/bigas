"""Idempotency cache for Jira automation webhook deliveries."""

from __future__ import annotations

import threading
import time
from typing import Set


class IdempotencyCache:
    """
    Remember processed keys for TTL seconds (process-local).
    Prevents Automation retries from double-running AI.
    """

    def __init__(self, *, ttl_s: int = 86400, max_keys: int = 5000):
        self._ttl_s = max(60, ttl_s)
        self._max_keys = max(100, max_keys)
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}

    def _purge(self, now: float) -> None:
        expired = [k for k, ts in self._seen.items() if now - ts > self._ttl_s]
        for k in expired:
            del self._seen[k]
        if len(self._seen) > self._max_keys:
            # drop oldest
            for k, _ in sorted(self._seen.items(), key=lambda kv: kv[1])[
                : len(self._seen) - self._max_keys
            ]:
                del self._seen[k]

    def already_processed(self, key: str) -> bool:
        if not key:
            return False
        now = time.time()
        with self._lock:
            self._purge(now)
            return key in self._seen

    def mark_processed(self, key: str) -> None:
        if not key:
            return
        now = time.time()
        with self._lock:
            self._purge(now)
            self._seen[key] = now

    def keys(self) -> Set[str]:
        with self._lock:
            return set(self._seen.keys())
