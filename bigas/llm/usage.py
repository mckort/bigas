"""
Token usage helpers and rough USD cost estimates for Gemini/OpenAI models.

Estimates use public list prices and are for operational visibility only —
not a substitute for Cloud Billing invoices.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, Mapping, Optional, Tuple


@dataclass(frozen=True)
class TokenUsage:
    """Token counts from a single LLM completion (provider-reported)."""

    prompt_tokens: Optional[int] = None
    candidates_tokens: Optional[int] = None
    thoughts_tokens: Optional[int] = None
    total_tokens: Optional[int] = None

    def merge(self, other: "TokenUsage") -> "TokenUsage":
        """Sum numeric fields; missing values stay missing unless the other side has them."""

        def _add(a: Optional[int], b: Optional[int]) -> Optional[int]:
            if a is None and b is None:
                return None
            return (a or 0) + (b or 0)

        return TokenUsage(
            prompt_tokens=_add(self.prompt_tokens, other.prompt_tokens),
            candidates_tokens=_add(self.candidates_tokens, other.candidates_tokens),
            thoughts_tokens=_add(self.thoughts_tokens, other.thoughts_tokens),
            total_tokens=_add(self.total_tokens, other.total_tokens),
        )

    def as_dict(self) -> Dict[str, Optional[int]]:
        return asdict(self)

    @property
    def has_counts(self) -> bool:
        return any(getattr(self, f.name) is not None for f in fields(self))


# (input_usd_per_mtok, output_usd_per_mtok) — output includes thinking tokens.
# Prices are approximate Google list rates for prompts <= 200k where applicable.
_MODEL_PRICE_USD_PER_MTOK: Tuple[Tuple[str, float, float], ...] = (
    # More specific prefixes first.
    ("gemini-3.1-pro", 2.00, 12.00),
    ("gemini-3-pro", 2.00, 12.00),
    ("gemini-pro-latest", 2.00, 12.00),
    ("gemini-2.5-pro", 1.25, 10.00),
    ("gemini-3.6-flash", 1.50, 7.50),
    ("gemini-3.5-flash-lite", 0.30, 2.50),
    ("gemini-3.5-flash", 1.50, 9.00),
    ("gemini-3.1-flash-lite", 0.25, 1.50),
    ("gemini-3-flash", 0.50, 3.00),
    ("gemini-flash-latest", 1.50, 7.50),
    ("gemini-2.5-flash-lite", 0.10, 0.40),
    ("gemini-2.5-flash", 0.30, 2.50),
    ("gemini-2.0-flash", 0.10, 0.40),
    ("gpt-4.1-mini", 0.40, 1.60),
    ("gpt-4.1", 2.00, 8.00),
    ("gpt-4o-mini", 0.15, 0.60),
    ("gpt-4o", 2.50, 10.00),
)


def resolve_model_price_usd_per_mtok(model: str) -> Optional[Tuple[float, float]]:
    """Return (input, output) USD per 1M tokens, or None if unknown."""
    name = (model or "").strip().lower()
    if not name:
        return None
    for prefix, inp, out in _MODEL_PRICE_USD_PER_MTOK:
        if name == prefix or name.startswith(prefix):
            return inp, out
    if name.startswith("gemini") and "flash-lite" in name:
        return 0.30, 2.50
    if name.startswith("gemini") and "flash" in name:
        return 1.50, 7.50
    if name.startswith("gemini") and "pro" in name:
        return 2.00, 12.00
    return None


def billed_output_tokens(usage: TokenUsage) -> Optional[int]:
    """Tokens billed as output (visible text + thinking).

    Gemini Pro often reports thinking only inside ``total_token_count``:
    ``thoughts_token_count`` is empty and ``candidates_token_count`` is the
    visible reply. Prefer ``total − prompt`` when total is present so we do
    not undercount. Never add ``thoughts_tokens`` on top of total.
    """
    prompt = usage.prompt_tokens
    if usage.total_tokens is not None and prompt is not None and usage.total_tokens >= prompt:
        return max(0, usage.total_tokens - prompt)
    if usage.candidates_tokens is not None:
        thoughts = usage.thoughts_tokens if usage.thoughts_tokens is not None else 0
        return usage.candidates_tokens + thoughts
    if usage.thoughts_tokens is not None:
        return usage.thoughts_tokens
    return None


def estimate_cost_usd(model: str, usage: TokenUsage) -> Optional[float]:
    """
    Estimate USD cost for one completion.

    Thinking tokens are billed as output. When ``total_tokens`` is present,
    billed output is ``total − prompt`` (same rule as VC Field Assistant).
    Otherwise candidates + thoughts, without double-counting total.
    """
    prices = resolve_model_price_usd_per_mtok(model)
    if prices is None or not usage.has_counts:
        return None
    input_rate, output_rate = prices

    prompt = usage.prompt_tokens
    if prompt is None:
        return None

    output = billed_output_tokens(usage)
    if output is None:
        return None

    cost = (prompt / 1_000_000.0) * input_rate + (output / 1_000_000.0) * output_rate
    return round(cost, 6)


def usage_from_mapping(raw: Optional[Mapping[str, Any]]) -> TokenUsage:
    """Normalize provider usage dicts (Gemini usage_metadata / OpenAI usage)."""
    if not raw:
        return TokenUsage()

    def _int(key_options: Tuple[str, ...]) -> Optional[int]:
        for key in key_options:
            if key not in raw:
                continue
            val = raw[key]
            if val is None:
                continue
            try:
                return int(val)
            except (TypeError, ValueError):
                continue
        return None

    prompt = _int(("prompt_token_count", "promptTokenCount", "prompt_tokens", "input_tokens"))
    candidates = _int(
        (
            "candidates_token_count",
            "candidatesTokenCount",
            "completion_tokens",
            "output_tokens",
            "candidates_tokens",
        )
    )
    thoughts = _int(("thoughts_token_count", "thoughtsTokenCount", "thoughts_tokens"))
    total = _int(("total_token_count", "totalTokenCount", "total_tokens"))
    return TokenUsage(
        prompt_tokens=prompt,
        candidates_tokens=candidates,
        thoughts_tokens=thoughts,
        total_tokens=total,
    )


def usage_log_payload(
    *,
    feature: str,
    model: str,
    usage: TokenUsage,
    extra: Optional[Dict[str, Any]] = None,
    **fields: Any,
) -> Dict[str, Any]:
    """Build a JSON-serializable structured log payload."""
    payload: Dict[str, Any] = {
        "event": "llm_usage",
        "feature": feature,
        "model": model,
        **usage.as_dict(),
    }
    est = estimate_cost_usd(model, usage)
    if est is not None:
        payload["est_cost_usd"] = est
        payload["cost_estimate"] = True
    if extra:
        payload.update(extra)
    if fields:
        payload.update(fields)
    return payload
