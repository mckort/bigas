from __future__ import annotations

import logging
import warnings
from typing import Any, Dict, List, Optional

# Suppress FutureWarnings from Google libs (e.g. Python 3.10 EOL) when using Gemini
warnings.filterwarnings("ignore", category=FutureWarning, module="google.api_core")
try:
    warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
    import google.generativeai as genai  # type: ignore
    try:
        from google.generativeai.types import HarmCategory, HarmBlockThreshold  # type: ignore
        _SAFETY_BLOCK_NONE = [
            {"category": HarmCategory.HARM_CATEGORY_HARASSMENT, "threshold": HarmBlockThreshold.BLOCK_NONE},
            {"category": HarmCategory.HARM_CATEGORY_HATE_SPEECH, "threshold": HarmBlockThreshold.BLOCK_NONE},
            {"category": HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, "threshold": HarmBlockThreshold.BLOCK_NONE},
            {"category": HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, "threshold": HarmBlockThreshold.BLOCK_NONE},
        ]
    except (ImportError, AttributeError):
        _SAFETY_BLOCK_NONE = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
except ImportError:  # pragma: no cover - optional dependency
    genai = None  # type: ignore
    _SAFETY_BLOCK_NONE = []

from bigas.llm.client import LLMClient

logger = logging.getLogger(__name__)


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
        # Split system instruction from conversation so we match browser/API behavior.
        # System message(s) go into GenerativeModel(system_instruction=...); the rest are chat.
        system_parts: List[str] = []
        rest: List[Dict[str, Any]] = []
        for m in messages:
            role = (m.get("role") or "user").lower()
            content = (m.get("content") or "").strip()
            if not content:
                continue
            if role == "system":
                system_parts.append(content)
            else:
                rest.append({"role": "model" if role == "assistant" else "user", "parts": [content]})

        if not rest:
            return ""

        generation_config = {}
        if max_tokens is not None:
            generation_config["max_output_tokens"] = max_tokens
        if temperature is not None:
            generation_config["temperature"] = temperature
        extra_cfg = kwargs.pop("generation_config", None)
        if isinstance(extra_cfg, dict):
            generation_config.update(extra_cfg)
        gen_cfg = generation_config if generation_config else None

        # Use a model with system_instruction when we have system message(s).
        system_instruction = "\n\n".join(system_parts) if system_parts else None
        if system_instruction:
            model = genai.GenerativeModel(
                self._model_name,
                system_instruction=system_instruction,
            )
        else:
            model = self._model

        # Single user message: generate_content. Multi-turn: start_chat + send_message.
        try:
            if len(rest) == 1 and rest[0]["role"] == "user":
                prompt_text = rest[0]["parts"][0]
                response = model.generate_content(
                    prompt_text,
                    generation_config=gen_cfg,
                    safety_settings=_SAFETY_BLOCK_NONE if _SAFETY_BLOCK_NONE else None,
                    **kwargs,
                )
            else:
                history = rest[:-1]
                to_send = rest[-1]["parts"][0]
                chat = model.start_chat(history=history)
                response = chat.send_message(
                    to_send,
                    generation_config=gen_cfg,
                    safety_settings=_SAFETY_BLOCK_NONE if _SAFETY_BLOCK_NONE else None,
                    **kwargs,
                )
        except Exception as e:
            logger.error("Gemini API call failed in complete()", exc_info=True)
            raise

        # Inspect candidates/parts for text content.
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return ""
        candidate = candidates[0]
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) if content else None or []

        text_parts: List[str] = []
        for part in parts:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                text_parts.append(part_text.strip())
        if text_parts:
            return "\n".join(text_parts)
        finish_reason = getattr(candidate, "finish_reason", None)
        safety_ratings = getattr(candidate, "safety_ratings", None)
        logger.warning(
            "Gemini response had no text parts (finish_reason=%s, safety_ratings=%s).",
            finish_reason,
            safety_ratings,
        )
        return ""

