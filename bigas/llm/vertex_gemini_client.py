"""
Vertex AI Gemini client using Application Default Credentials (ADC).

Use this when GEMINI_API_KEY is not set or not allowed (e.g. Cloud Run with
"API keys are not supported" / OAuth required). The Cloud Run service account
is used automatically.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from bigas.llm.client import LLMClient


def _get_vertex_model(project: str, location: str, model_name: str):  # noqa: ANN201
    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel, GenerationConfig
    except ImportError as e:
        raise RuntimeError(
            "google-cloud-aiplatform is not installed. "
            "Add it to requirements to use Vertex AI Gemini."
        ) from e
    vertexai.init(project=project, location=location)
    return GenerativeModel(model_name), GenerationConfig


class VertexGeminiLLMClient(LLMClient):
    """
    LLMClient implementation using Vertex AI Gemini with ADC (no API key).

    Requires GOOGLE_PROJECT_ID and a Vertex AI region (VERTEX_AI_LOCATION or
    default europe-west1). The service account (e.g. Cloud Run's) must have
    Vertex AI User (or similar) permission.
    """

    def __init__(
        self,
        *,
        project: str,
        location: str,
        model: str,
    ) -> None:
        self._model, self._gen_config = _get_vertex_model(project, location, model)
        self._model_name = model
        self._location = location

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
        prompt_parts = []
        for m in messages:
            role = m.get("role", "").upper() or "USER"
            content = m.get("content") or ""
            prompt_parts.append(f"{role}: {content}")
        prompt = "\n".join(prompt_parts)

        config_kw: Dict[str, Any] = {}
        if max_tokens is not None:
            config_kw["max_output_tokens"] = max_tokens
        if temperature is not None:
            config_kw["temperature"] = temperature
        generation_config = self._gen_config(**config_kw) if config_kw else None

        response = self._model.generate_content(
            prompt,
            generation_config=generation_config,
            **kwargs,
        )
        text = getattr(response, "text", None)
        if text:
            return text.strip()
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            return str(candidates[0])
        return ""
