from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from bigas.llm.usage import TokenUsage


@dataclass(frozen=True)
class LLMCompletion:
    """Result of a single LLM completion call."""

    text: str
    finish_reason: Optional[str] = None
    usage: TokenUsage = field(default_factory=TokenUsage)

    @property
    def truncated(self) -> bool:
        """True when the provider stopped because the output token budget was hit."""
        reason = (self.finish_reason or "").upper()
        return "MAX_TOKEN" in reason
