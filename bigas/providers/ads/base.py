"""
AdsProvider ABC — implement this to add a new ad platform.

Multiple AdsProvider implementations can be active simultaneously.
Each instance represents one ad account on one platform.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CampaignMetrics:
    campaign_id: str
    campaign_name: str
    impressions: int
    clicks: int
    spend: float
    currency: str
    conversions: Optional[int] = None
    ctr: Optional[float] = None       # clicks / impressions
    cpc: Optional[float] = None       # spend / clicks
    cpm: Optional[float] = None       # spend / impressions * 1000
    extra: dict = field(default_factory=dict)   # platform-specific fields


class AdsProvider(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. 'linkedin', 'tiktok'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g. 'LinkedIn Ads'."""

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        """Return True only when all required env vars are present."""

    @abstractmethod
    def get_campaign_performance(
        self,
        start_date: str,
        end_date: str,
    ) -> List[CampaignMetrics]:
        """Return per-campaign metrics for the period."""

    @abstractmethod
    def get_account_summary(self, start_date: str, end_date: str) -> dict:
        """Return account-level totals: total_spend, total_clicks, total_impressions."""

    def health_check(self) -> dict:
        return {"status": "ok", "provider": self.name}

