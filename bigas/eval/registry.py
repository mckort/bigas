"""Model discovery, pricing, and elimination state for eval runs."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import requests

from bigas.eval.storage import EvalStorage
from bigas.llm.usage import TokenUsage, estimate_cost_usd, resolve_model_price_usd_per_mtok

logger = logging.getLogger(__name__)

STATE_BLOB = "model_eval_state.json"

# Fallback flagship models when provider list APIs are unavailable.
DEFAULT_PRO_MODELS: Tuple[Tuple[str, str], ...] = (
    ("openai", "gpt-4o"),
    ("openai", "gpt-5"),
    ("anthropic", "claude-sonnet-4-20250514"),
    ("anthropic", "claude-opus-4-20250514"),
    ("gemini", "gemini-2.5-pro"),
    ("gemini", "gemini-3.1-pro-preview"),
)

# Anthropic list prices (USD / 1M tokens) — input, output.
_ANTHROPIC_PRICE_USD_PER_MTOK: Tuple[Tuple[str, float, float], ...] = (
    ("claude-opus-4", 15.00, 75.00),
    ("claude-sonnet-4", 3.00, 15.00),
    ("claude-3-5-sonnet", 3.00, 15.00),
    ("claude-3-opus", 15.00, 75.00),
)

_PRO_PATTERNS = re.compile(
    r"(pro|opus|sonnet|gpt-4|gpt-5|o1|o3|o4|flagship|latest)",
    re.IGNORECASE,
)
_EXCLUDE_PATTERNS = re.compile(
    r"(embed|embedding|whisper|tts|dall-e|realtime|audio|transcribe|moderation|instruct|mini|nano|lite|flash-lite|preview-tts)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ModelCandidate:
    provider: str
    model_id: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model_id}"


def _is_pro_model(model_id: str) -> bool:
    name = (model_id or "").strip()
    if not name or _EXCLUDE_PATTERNS.search(name):
        return False
    return bool(_PRO_PATTERNS.search(name))


def _discover_openai_models() -> List[ModelCandidate]:
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return []
    try:
        resp = requests.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        out: List[ModelCandidate] = []
        for item in data.get("data") or []:
            model_id = (item.get("id") or "").strip()
            if _is_pro_model(model_id):
                out.append(ModelCandidate("openai", model_id))
        return out
    except Exception as exc:
        logger.warning("OpenAI model discovery failed: %s", exc)
        return []


def _discover_gemini_models() -> List[ModelCandidate]:
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return []
    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        out: List[ModelCandidate] = []
        for model in genai.list_models():
            name = (getattr(model, "name", "") or "").replace("models/", "")
            if _is_pro_model(name):
                out.append(ModelCandidate("gemini", name))
        return out
    except Exception as exc:
        logger.warning("Gemini model discovery failed: %s", exc)
        return []


def _discover_anthropic_models() -> List[ModelCandidate]:
    """Anthropic has no public list-models endpoint in Bigas deps — use known pro models."""
    return [
        ModelCandidate(provider, model_id)
        for provider, model_id in DEFAULT_PRO_MODELS
        if provider == "anthropic"
    ]


def discover_pro_models() -> List[ModelCandidate]:
    """Return deduplicated flagship/pro models across supported providers."""
    seen: Set[str] = set()
    candidates: List[ModelCandidate] = []

    for source in (_discover_openai_models, _discover_gemini_models, _discover_anthropic_models):
        for item in source():
            if item.key in seen:
                continue
            seen.add(item.key)
            candidates.append(item)

    return candidates


def resolve_anthropic_price_usd_per_mtok(model_id: str) -> Optional[Tuple[float, float]]:
    name = (model_id or "").strip().lower()
    for prefix, inp, out in _ANTHROPIC_PRICE_USD_PER_MTOK:
        if name == prefix or name.startswith(prefix):
            return inp, out
    if "opus" in name:
        return 15.00, 75.00
    if "sonnet" in name:
        return 3.00, 15.00
    return None


def estimate_model_cost_usd(
    provider: str,
    model_id: str,
    *,
    prompt_tokens: int,
    output_tokens: int,
) -> Optional[float]:
    if prompt_tokens <= 0 and output_tokens <= 0:
        return None
    provider = (provider or "").strip().lower()
    if provider == "anthropic":
        prices = resolve_anthropic_price_usd_per_mtok(model_id)
        if prices is None:
            return None
        inp_rate, out_rate = prices
        return round(
            (prompt_tokens / 1_000_000.0) * inp_rate + (output_tokens / 1_000_000.0) * out_rate,
            6,
        )
    if provider in ("openai", "gemini"):
        usage = TokenUsage(prompt_tokens=prompt_tokens, candidates_tokens=output_tokens)
        if usage.total_tokens is None:
            usage = TokenUsage(
                prompt_tokens=prompt_tokens,
                candidates_tokens=output_tokens,
                total_tokens=prompt_tokens + output_tokens,
            )
        return estimate_cost_usd(model_id, usage)
    # Unknown provider — try generic pricing table by model id.
    if resolve_model_price_usd_per_mtok(model_id):
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            candidates_tokens=output_tokens,
            total_tokens=prompt_tokens + output_tokens,
        )
        return estimate_cost_usd(model_id, usage)
    return None


def load_eval_state(storage: Optional[EvalStorage] = None) -> Dict[str, Any]:
    store = storage or EvalStorage()
    data = store.get_json(STATE_BLOB)
    if not isinstance(data, dict):
        return {"use_cases": {}}
    if "use_cases" not in data:
        data["use_cases"] = {}
    return data


def save_eval_state(state: Dict[str, Any], storage: Optional[EvalStorage] = None) -> None:
    store = storage or EvalStorage()
    store.store_json(STATE_BLOB, state)


def get_candidate_models(
    use_case: str,
    *,
    storage: Optional[EvalStorage] = None,
    explicit_models: Optional[List[str]] = None,
) -> List[ModelCandidate]:
    """
    Return models to benchmark: reigning champion + newly discovered pro models.
    Skips models previously eliminated for this use case.
    """
    if explicit_models:
        out: List[ModelCandidate] = []
        for raw in explicit_models:
            text = (raw or "").strip()
            if not text:
                continue
            if ":" in text:
                provider, model_id = text.split(":", 1)
                out.append(ModelCandidate(provider.strip().lower(), model_id.strip()))
            else:
                provider = _infer_provider(text)
                out.append(ModelCandidate(provider, text))
        return out

    state = load_eval_state(storage)
    uc_state = (state.get("use_cases") or {}).get(use_case) or {}
    champion = (uc_state.get("champion") or "").strip()
    eliminated: Set[str] = set(uc_state.get("eliminated") or [])

    discovered = discover_pro_models()
    by_id: Dict[str, ModelCandidate] = {m.model_id: m for m in discovered}

    candidates: List[ModelCandidate] = []
    seen: Set[str] = set()

    def _add(model: ModelCandidate) -> None:
        if model.key in eliminated or model.key in seen:
            return
        seen.add(model.key)
        candidates.append(model)

    if champion:
        if ":" in champion:
            provider, model_id = champion.split(":", 1)
            _add(ModelCandidate(provider.strip().lower(), model_id.strip()))
        elif champion in by_id:
            _add(by_id[champion])
        else:
            _add(ModelCandidate(_infer_provider(champion), champion))

    for model in discovered:
        _add(model)

    return candidates


def update_eval_state_after_run(
    use_case: str,
    ranked: List[ModelCandidate],
    *,
    storage: Optional[EvalStorage] = None,
) -> str:
    """Update champion and eliminated models after a ranked run."""
    if not ranked:
        return ""

    state = load_eval_state(storage)
    use_cases = state.setdefault("use_cases", {})
    uc_state = use_cases.setdefault(use_case, {"champion": "", "eliminated": []})
    eliminated: Set[str] = set(uc_state.get("eliminated") or [])

    champion = ranked[0]
    champion_key = champion.key
    uc_state["champion"] = champion_key

    for loser in ranked[1:]:
        eliminated.add(loser.key)
    uc_state["eliminated"] = sorted(eliminated)
    save_eval_state(state, storage)
    return champion_key


def _infer_provider(model_id: str) -> str:
    lower = (model_id or "").lower()
    if lower.startswith("gpt-") or "gpt" in lower or lower.startswith("o"):
        return "openai"
    if lower.startswith("claude"):
        return "anthropic"
    if lower.startswith("gemini"):
        return "gemini"
    return "unknown"
