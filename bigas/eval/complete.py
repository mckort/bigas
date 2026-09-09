"""Complete a candidate eval model (OpenAI, Gemini, Anthropic)."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from bigas.eval.registry import _infer_provider
from bigas.llm.anthropic_client import complete_anthropic_chat
from bigas.llm.completion import LLMCompletion


def complete_eval_model(
    model_id: str,
    prompt: str,
    *,
    max_tokens: Optional[int] = None,
    temperature: float = 0.2,
) -> LLMCompletion:
    """Run one eval step against the candidate model id."""
    return complete_eval_chat(
        model_id,
        _messages(prompt),
        max_tokens=max_tokens,
        temperature=temperature,
    )


def complete_eval_chat(
    model_id: str,
    messages: List[dict],
    *,
    tools: Optional[List[dict]] = None,
    max_tokens: Optional[int] = None,
    temperature: float = 0.2,
) -> LLMCompletion:
    """Multi-turn eval complete. ``tools`` is the OpenAI function-calling shape."""
    budget = max_tokens if max_tokens is not None else _step_max_tokens()
    provider = _infer_provider(model_id)
    if provider == "anthropic":
        return complete_anthropic_chat(
            model_id,
            messages,
            tools=tools,
            max_tokens=budget,
            temperature=temperature,
        )
    from bigas.llm.factory import get_llm_client

    client, _resolved = get_llm_client(feature="model_eval", explicit_model=model_id)
    kwargs: Dict[str, Any] = {}
    if tools:
        kwargs["tools"] = tools
    return client.complete_detailed(
        messages=messages,
        max_tokens=budget,
        temperature=temperature,
        **kwargs,
    )


class EvalChatClient:
    """``complete_detailed`` adapter so eval candidates can drive the goal loop."""

    def __init__(self, model_id: str):
        self.model_id = model_id

    def complete_detailed(self, messages, *, max_tokens=None, temperature=None, **kwargs):
        return complete_eval_chat(
            self.model_id,
            messages,
            tools=kwargs.get("tools"),
            max_tokens=max_tokens,
            temperature=0.2 if temperature is None else temperature,
        )


def _messages(prompt: str) -> List[dict]:
    return [{"role": "user", "content": prompt}]


def _step_max_tokens() -> int:
    raw = (os.environ.get("EVAL_STEP_MAX_TOKENS") or "8192").strip()
    try:
        return max(256, int(raw))
    except ValueError:
        return 8192
