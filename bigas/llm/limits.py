from __future__ import annotations

from typing import Optional

# OpenAI chat models with >=8192 output tokens (prefix match, longest first).
_OPENAI_HIGH_OUTPUT_PREFIXES = (
    "gpt-4o-mini",
    "gpt-4o-2024-08",
    "gpt-4.1",
    "gpt-4.5",
    "o3-mini",
    "o3",
    "o1-mini",
    "o1",
)

# Legacy OpenAI chat models capped at 4096 output tokens.
_OPENAI_LOW_OUTPUT_PREFIXES = (
    "gpt-4-turbo",
    "gpt-4",
    "gpt-3.5",
)


def model_output_token_limit(model: str) -> Optional[int]:
    """
    Return a known max *output* token ceiling for ``model``, or None when unknown.

    Gemini models used by Bigas support at least 8192 output tokens; OpenAI limits
    vary by model generation (many legacy gpt-4 variants stop at 4096).
    """
    name = (model or "").strip().lower()
    if not name:
        return None
    if name.startswith("gemini"):
        return None
    if name.startswith("gpt-"):
        for prefix in _OPENAI_HIGH_OUTPUT_PREFIXES:
            if name.startswith(prefix):
                return 16_384
        for prefix in _OPENAI_LOW_OUTPUT_PREFIXES:
            if name.startswith(prefix):
                return 4096
        # Unknown gpt-* — assume legacy 4096 cap to avoid 400s in production.
        return 4096
    return None


def uses_max_completion_tokens(model: str) -> bool:
    """True when Chat Completions rejects ``max_tokens`` for this model id."""
    name = (model or "").strip().lower()
    if not name:
        return False
    if name.startswith(("o1", "o3", "o4")):
        return True
    return name.startswith("gpt-5") or name.startswith("gpt-6")


def supports_temperature(model: str) -> bool:
    """True when Chat Completions accepts a custom ``temperature`` for this model id."""
    name = (model or "").strip().lower()
    if not name:
        return True
    return not name.startswith(("o1", "o3", "o4"))


def cap_output_tokens(model: str, requested: int) -> int:
    """Return ``requested`` capped to the model's known output limit."""
    if requested <= 0:
        return requested
    limit = model_output_token_limit(model)
    if limit is None:
        return requested
    return min(requested, limit)
