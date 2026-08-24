from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from bigas.llm.usage import TokenUsage


@dataclass(frozen=True)
class ToolCall:
    """A provider-normalized function/tool call from one LLM turn."""

    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMCompletion:
    """Result of a single LLM completion call."""

    text: str
    finish_reason: Optional[str] = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    tool_calls: Tuple[ToolCall, ...] = ()

    @property
    def truncated(self) -> bool:
        """True when the provider stopped because the output token budget was hit."""
        reason = (self.finish_reason or "").upper()
        return "MAX_TOKEN" in reason
