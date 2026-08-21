"""Wrap an LLM client to emit ``llm_usage`` structured logs on every call."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from bigas.llm.client import LLMClient
from bigas.llm.completion import LLMCompletion
from bigas.llm.usage import TokenUsage, usage_from_mapping, usage_log_payload

logger = logging.getLogger(__name__)


class LoggingLLMClient(LLMClient):
    """
    Delegates to an inner LLMClient and logs list-price usage after each call.

    ``complete()`` goes through ``complete_detailed()`` so chat and other
    call sites that only use ``complete()`` are covered. Logging never raises.
    """

    def __init__(self, inner: Any, *, feature: str, model: str) -> None:
        self._inner = inner
        self._feature = feature
        self._model = model

    @property
    def model_name(self) -> str:
        return getattr(self._inner, "model_name", None) or self._model

    @property
    def _client(self) -> Any:
        # Chief-of-staff GPT tool-calling reads llm._client directly.
        if not hasattr(self._inner, "_client"):
            raise AttributeError("_client")
        return self._inner._client

    def complete(
        self,
        messages: List[Dict[str, str]],
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> str:
        return self.complete_detailed(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        ).text

    def complete_detailed(
        self,
        messages: List[Dict[str, str]],
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> LLMCompletion:
        result = self._inner.complete_detailed(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )
        self._log_usage(getattr(result, "usage", None))
        return result

    def record_openai_response(self, response: Any) -> None:
        """Log usage from an OpenAI Chat Completions response (tool-calling loop)."""
        usage_obj = getattr(response, "usage", None)
        if usage_obj is None:
            return
        if isinstance(usage_obj, dict):
            usage = usage_from_mapping(usage_obj)
        else:
            usage = usage_from_mapping(
                {
                    "prompt_tokens": getattr(usage_obj, "prompt_tokens", None),
                    "completion_tokens": getattr(usage_obj, "completion_tokens", None),
                    "total_tokens": getattr(usage_obj, "total_tokens", None),
                }
            )
        self._log_usage(usage)

    def _log_usage(self, usage: Optional[TokenUsage]) -> None:
        if usage is None or not usage.has_counts:
            return
        try:
            logger.info(
                json.dumps(
                    usage_log_payload(
                        feature=self._feature,
                        model=self._model,
                        usage=usage,
                    ),
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
        except Exception:
            logger.warning("Failed to emit llm_usage log", exc_info=True)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
