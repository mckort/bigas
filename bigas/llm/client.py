from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol


class LLMClient(Protocol):
    """
    Minimal provider-agnostic interface for chat-style LLMs.

    Implementations should wrap a specific provider (OpenAI, Gemini, etc.)
    and expose a unified `complete` method.
    """

    def complete(
        self,
        messages: List[Dict[str, str]],
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> str:
        ...

