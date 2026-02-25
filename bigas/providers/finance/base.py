"""
FinanceProvider ABC — implement this to add a new finance data source.

Required env vars are declared in the concrete class's `is_configured()`.
All monetary values are returned as floats in the account's native currency.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Transaction:
    id: str
    date: str                   # ISO 8601: YYYY-MM-DD
    amount: float               # positive = income, negative = expense
    currency: str               # ISO 4217, e.g. "SEK", "USD"
    description: str
    category: Optional[str] = None
    counterpart: Optional[str] = None


@dataclass
class PeriodSummary:
    start_date: str
    end_date: str
    total: float
    currency: str
    breakdown: dict = field(default_factory=dict)   # category -> float


class FinanceProvider(ABC):

    # ── Identity ─────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. 'fortnox', 'quickbooks'. Used in logs and manifests."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g. 'Fortnox', 'QuickBooks Online'."""

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        """Return True only when all required env vars are present and non-empty."""

    # ── Domain methods ────────────────────────────────────────────────────────

    @abstractmethod
    def get_revenue(self, start_date: str, end_date: str) -> PeriodSummary:
        """Return total revenue for the period."""

    @abstractmethod
    def get_expenses(self, start_date: str, end_date: str) -> PeriodSummary:
        """Return total expenses for the period."""

    @abstractmethod
    def get_transactions(
        self,
        start_date: str,
        end_date: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Transaction]:
        """Return a paginated list of transactions for the period."""

    # ── Optional — override for richer capability ─────────────────────────────

    def health_check(self) -> dict:
        """Return {'status': 'ok'} or {'status': 'error', 'message': '...'}."""
        return {"status": "ok", "provider": self.name}

    def get_mrr(self) -> Optional[float]:
        """Return current MRR if the provider supports it, else None."""
        return None

