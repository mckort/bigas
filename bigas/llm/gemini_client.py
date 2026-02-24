from __future__ import annotations

import warnings
from typing import Any, Dict, List, Optional

# Suppress FutureWarnings from Google libs (e.g. Python 3.10 EOL) when using Gemini
warnings.filterwarnings("ignore", category=FutureWarning, module="google.api_core")
try:
    warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
    import google.generativeai as genai  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    genai = None  # type: ignore

from bigas.llm.client import LLMClient


class GeminiLLMClient(LLMClient):
    """
    LLMClient implementation backed by Google's Gemini API.

    This uses the `google-generativeai` package and assumes an API key
    is provided explicitly (no implicit ADC in this helper).
    """

    def __init__(self, *, api_key: str, model: str) -> None:
        if genai is None:
            raise RuntimeError(
                "google-generativeai is not installed. "
                "Add it to requirements to use Gemini."
            )
        genai.configure(api_key=api_key)
        self._model_name = model
        self._model = genai.GenerativeModel(model)

    @property
    def model_name(self) -> str:
        return self._model_name

    def complete(
        self,
        messages: List[Dict[str, str]],
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> str:
        # Simple mapping: flatten chat messages into a single prompt.
        prompt_parts = []
        for m in messages:
            role = m.get("role", "").upper() or "USER"
            content = m.get("content") or ""
            prompt_parts.append(f"{role}: {content}")
        prompt = "\n".join(prompt_parts)

        generation_config = {}
        if max_tokens is not None:
            generation_config["max_output_tokens"] = max_tokens
        if temperature is not None:
            generation_config["temperature"] = temperature
        extra_cfg = kwargs.pop("generation_config", None)
        if isinstance(extra_cfg, dict):
            generation_config.update(extra_cfg)

        response = self._model.generate_content(
            prompt,
            generation_config=generation_config if generation_config else None,
            **kwargs,
        )
        text = getattr(response, "text", None)
        if not text:
            # Fallback: join parts if structured response
            parts = getattr(response, "candidates", None) or []
            if parts:
                return str(parts[0])
            return ""
        return text.strip()

