from __future__ import annotations

import os
from typing import Optional, Tuple

from bigas.llm.client import LLMClient
from bigas.llm.openai_client import OpenAILLMClient
from bigas.llm.gemini_client import GeminiLLMClient


def _infer_provider_from_model(model: str) -> str:
    lower = model.lower()
    if lower.startswith("gpt-") or "gpt" in lower:
        return "openai"
    if lower.startswith("gemini-") or "gemini" in lower:
        return "gemini"
    return os.environ.get("BIGAS_LLM_PROVIDER", "openai").lower()


def get_llm_client(
    *,
    feature: str,
    explicit_model: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    gemini_api_key: Optional[str] = None,
) -> Tuple[LLMClient, str]:
    """
    Return an LLM client and the resolved model name for a given feature.

    Resolution order for the model:
      1. explicit_model (e.g. request body llm_model)
      2. per-feature env override
      3. LLM_MODEL (provider-agnostic)
      4. hard-coded default "gpt-4o"

    Optional openai_api_key / gemini_api_key override env (e.g. for per-tenant keys in SaaS).
    """
    feature_env_map = {
        "cto_pr_review": "BIGAS_CTO_PR_REVIEW_MODEL",
        "progress_updates": "BIGAS_PROGRESS_UPDATES_MODEL",
        "release_notes": "BIGAS_RELEASE_NOTES_MODEL",
        "marketing": "BIGAS_MARKETING_LLM_MODEL",
        "duplicate_recommendation": "BIGAS_DUPLICATE_RECOMMENDATION_MODEL",
    }
    feature_env = feature_env_map.get(feature)

    model_env = os.environ.get("LLM_MODEL")
    model = (
        explicit_model
        or (os.environ.get(feature_env) if feature_env else None)
        or model_env
        or "gpt-4o"
    ).strip()

    provider = _infer_provider_from_model(model)

    if provider == "gemini":
        api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set for Gemini provider")
        client: LLMClient = GeminiLLMClient(api_key=api_key, model=model)
        return client, model

    # default to OpenAI
    api_key = openai_api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set for OpenAI provider")
    client = OpenAILLMClient(api_key=api_key, model=model)
    return client, model

