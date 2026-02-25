"""
AnalyticsProvider ABC — implement this to add a new web analytics source.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class PageMetrics:
    path: str
    sessions: int
    pageviews: int
    bounce_rate: Optional[float] = None
    avg_session_duration: Optional[float] = None


class AnalyticsProvider(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        ...

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        ...

    @abstractmethod
    def get_overview(self, start_date: str, end_date: str) -> dict:
        """Return top-level metrics: sessions, users, pageviews, bounce_rate."""

    @abstractmethod
    def get_top_pages(self, start_date: str, end_date: str, limit: int = 10) -> List[PageMetrics]:
        """Return the top N pages by sessions."""

    def health_check(self) -> dict:
        return {"status": "ok", "provider": self.name}

