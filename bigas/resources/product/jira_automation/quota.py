"""In-process daily quota for Jira AI runs (global across handlers)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional, Tuple


class DailyQuota:
    """
    Simple process-local counter. Good enough for a single Cloud Run instance trial.
    Resets on UTC day change. Not shared across multiple instances.
    """

    def __init__(self, limit: int = 20):
        self._limit = max(1, int(limit))
        self._lock = threading.Lock()
        self._day: Optional[str] = None
        self._count = 0

    @property
    def limit(self) -> int:
        return self._limit

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def snapshot(self) -> Tuple[int, int, str]:
        with self._lock:
            day = self._today()
            if self._day != day:
                self._day = day
                self._count = 0
            return self._count, self._limit, day

    def try_acquire(self) -> Tuple[bool, int, int]:
        """
        Attempt to consume one run.
        Returns (ok, used_after, limit).
        """
        with self._lock:
            day = self._today()
            if self._day != day:
                self._day = day
                self._count = 0
            if self._count >= self._limit:
                return False, self._count, self._limit
            self._count += 1
            return True, self._count, self._limit
