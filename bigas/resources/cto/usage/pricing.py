"""
List-price estimates for Cursor cloud-agent token usage.

Operational visibility only — not a substitute for Cursor invoices.
Prices are USD per 1M tokens. Cache rates follow public Cursor/model docs
where known; otherwise cache write ≈ input and cache read ≈ 0.1× input.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple


@dataclass(frozen=True)
class CursorTokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_write_tokens
            + self.cache_read_tokens
        )

    @classmethod
    def from_mapping(cls, raw: Optional[Mapping[str, Any]]) -> "CursorTokenUsage":
        if not raw:
            return cls()

        def _int(*keys: str) -> int:
            for key in keys:
                if key not in raw:
                    continue
                try:
                    return max(0, int(raw[key] or 0))
                except (TypeError, ValueError):
                    continue
            return 0

        return cls(
            input_tokens=_int("inputTokens", "input_tokens"),
            output_tokens=_int("outputTokens", "output_tokens"),
            cache_write_tokens=_int("cacheWriteTokens", "cache_write_tokens"),
            cache_read_tokens=_int("cacheReadTokens", "cache_read_tokens"),
        )


# (input, output, cache_write, cache_read) USD / 1M tokens
_CURSOR_PRICE_USD_PER_MTOK: Tuple[Tuple[str, float, float, float, float], ...] = (
    # Composer 2.5 standard (preferred for background/cloud agents).
    ("composer-2.5-fast", 3.00, 15.00, 3.00, 0.20),
    ("composer-2.5", 0.50, 2.50, 0.50, 0.20),
    ("composer-2-fast", 3.00, 15.00, 3.00, 0.20),
    ("composer-2", 0.50, 2.50, 0.50, 0.20),
    ("composer-1.5", 0.50, 2.50, 0.50, 0.20),
    ("composer", 0.50, 2.50, 0.50, 0.20),
    # Common third-party cloud-agent models (Cursor public list prices).
    ("claude-4.6-sonnet", 3.00, 15.00, 3.75, 0.30),
    ("claude-4.5-sonnet", 3.00, 15.00, 3.75, 0.30),
    ("claude-4-sonnet", 3.00, 15.00, 3.75, 0.30),
    ("claude-4.6-opus", 5.00, 25.00, 6.25, 0.50),
    ("claude-4.5-opus", 5.00, 25.00, 6.25, 0.50),
    ("gemini-3.1-pro", 2.00, 12.00, 2.00, 0.20),
    ("gemini-3-pro", 2.00, 12.00, 2.00, 0.20),
    ("gemini-2.5-pro", 1.25, 10.00, 1.25, 0.125),
    ("gemini-2.5-flash", 0.30, 2.50, 0.30, 0.03),
    ("gpt-5.4", 2.50, 15.00, 2.50, 0.25),
    ("gpt-5.2", 1.75, 14.00, 1.75, 0.175),
    ("gpt-5", 1.25, 10.00, 1.25, 0.125),
)


def resolve_cursor_price_usd_per_mtok(
    model: str,
) -> Optional[Tuple[float, float, float, float]]:
    """Return (input, output, cache_write, cache_read) USD/MTok, or None."""
    name = (model or "").strip().lower()
    if not name:
        return None
    for prefix, inp, out, cw, cr in _CURSOR_PRICE_USD_PER_MTOK:
        if name == prefix or name.startswith(prefix):
            return inp, out, cw, cr
    if "composer" in name and "fast" in name:
        return 3.00, 15.00, 3.00, 0.20
    if "composer" in name:
        return 0.50, 2.50, 0.50, 0.20
    return None


def estimate_cursor_cost_usd(model: str, usage: CursorTokenUsage) -> Optional[float]:
    prices = resolve_cursor_price_usd_per_mtok(model)
    if prices is None:
        return None
    if usage.total_tokens <= 0 and not any(
        (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens)
    ):
        return None
    inp_r, out_r, cw_r, cr_r = prices
    cost = (
        (usage.input_tokens / 1_000_000.0) * inp_r
        + (usage.output_tokens / 1_000_000.0) * out_r
        + (usage.cache_write_tokens / 1_000_000.0) * cw_r
        + (usage.cache_read_tokens / 1_000_000.0) * cr_r
    )
    return round(cost, 6)


def default_autofix_model() -> str:
    import os

    return (
        (os.environ.get("BIGAS_CTO_AUTOFIX_MODEL") or "").strip()
        or "composer-2.5"
    )
