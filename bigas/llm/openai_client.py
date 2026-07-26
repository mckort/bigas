from __future__ import annotations

from typing import Any, Dict, List, Optional

import openai

from bigas.llm.client import LLMClient
from bigas.llm.completion import LLMCompletion


class OpenAILLMClient(LLMClient):
    """
    LLMClient implementation backed by OpenAI's Chat Completions API.
    """

    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = openai.OpenAI(api_key=api_key)
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

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
        completion = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )
        choice = completion.choices[0]
        finish = getattr(choice, "finish_reason", None)
        return LLMCompletion(
            text=(choice.message.content or "").strip(),
            finish_reason=str(finish) if finish is not None else None,
        )
