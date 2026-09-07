"""Complete a candidate eval model (OpenAI, Gemini, Anthropic)."""
from __future__ import annotations

import os
from typing import List, Optional

import requests

from bigas.eval.registry import _infer_provider
from bigas.llm.completion import LLMCompletion
from bigas.llm.usage import TokenUsage


def complete_eval_model(
    model_id: str,
    prompt: str,
    *,
    max_tokens: Optional[int] = None,
    temperature: float = 0.2,
) -> LLMCompletion:
    """Run one eval step against the candidate model id."""
    budget = max_tokens if max_tokens is not None else _step_max_tokens()
    provider = _infer_provider(model_id)
    if provider == "anthropic":
        return _complete_anthropic(model_id, prompt, max_tokens=budget, temperature=temperature)
    from bigas.llm.factory import get_llm_client

    client, _resolved = get_llm_client(feature="model_eval", explicit_model=model_id)
    return client.complete_detailed(
        messages=_messages(prompt),
        max_tokens=budget,
        temperature=temperature,
    )


def _messages(prompt: str) -> List[dict]:
    return [{"role": "user", "content": prompt}]


def _step_max_tokens() -> int:
    raw = (os.environ.get("EVAL_STEP_MAX_TOKENS") or "8192").strip()
    try:
        return max(256, int(raw))
    except ValueError:
        return 8192


def _complete_anthropic(
    model_id: str,
    prompt: str,
    *,
    max_tokens: int,
    temperature: float,
) -> LLMCompletion:
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set; cannot eval Claude models")
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": _messages(prompt),
        },
        timeout=180,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Anthropic eval complete failed HTTP {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    parts = []
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    usage_raw = data.get("usage") or {}
    prompt_tokens = int(usage_raw.get("input_tokens") or 0)
    output_tokens = int(usage_raw.get("output_tokens") or 0)
    return LLMCompletion(
        text="".join(parts),
        finish_reason=str(data.get("stop_reason") or "") or None,
        usage=TokenUsage(
            prompt_tokens=prompt_tokens,
            candidates_tokens=output_tokens,
            total_tokens=prompt_tokens + output_tokens,
        ),
    )
