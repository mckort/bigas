"""
UsageProvider ABC — implement this to add a historical AI-usage data source.

Multiple UsageProvider implementations can be active simultaneously.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class UsageEvent:
    provider: str
    source_id: str
    started_at: str
    feature: str
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    est_cost_usd: Optional[float] = None
    cost_estimate: bool = True
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class UsageProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. 'cursor', 'llm_logs'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name."""

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        """Return True only when required env/credentials are present."""

    @abstractmethod
    def fetch_usage(
        self,
        *,
        start: datetime,
        end: datetime,
        feature_prefix: Optional[str] = None,
    ) -> List[UsageEvent]:
        """Return usage events in [start, end] (timezone-aware datetimes)."""

    def health_check(self) -> dict:
        return {"status": "ok", "provider": self.name}
