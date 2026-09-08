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

# Fallback when official model-overview pages cannot be fetched.
DEFAULT_PRO_MODELS: Tuple[Tuple[str, str], ...] = (
    ("openai", "gpt-6-astra"),
    ("openai", "gpt-5.6-sol"),
    ("anthropic", "claude-fable-5-1"),
    ("anthropic", "claude-opus-5"),
    ("gemini", "gemini-3.1-pro-preview"),
)

# Anthropic list prices (USD / 1M tokens) — input, output.
_ANTHROPIC_PRICE_USD_PER_MTOK: Tuple[Tuple[str, float, float], ...] = (
    ("claude-fable-5", 10.00, 50.00),
    ("claude-opus-5", 5.00, 25.00),
    ("claude-sonnet-5", 2.00, 10.00),
    ("claude-opus-4", 15.00, 75.00),
    ("claude-sonnet-4", 3.00, 15.00),
    ("claude-3-5-sonnet", 3.00, 15.00),
    ("claude-3-opus", 15.00, 75.00),
)

_CATALOG_EXCLUDE = re.compile(
    r"(embed|embedding|whisper|tts|dall-e|realtime|audio|transcribe|moderation|"
    r"instruct|image|sora|lyria|veo|imagen)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ModelCandidate:
    provider: str
    model_id: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model_id}"


def _keep_catalog_id(model_id: str) -> bool:
    name = (model_id or "").strip()
    return bool(name) and not _CATALOG_EXCLUDE.search(name)


def list_openai_model_ids() -> List[str]:
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
        out: List[str] = []
        for item in data.get("data") or []:
            model_id = (item.get("id") or "").strip()
            if _keep_catalog_id(model_id):
                out.append(model_id)
        return out
    except Exception as exc:
        logger.warning("OpenAI model catalog failed: %s", exc)
        return []


def list_gemini_model_ids() -> List[str]:
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return []
    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        out: List[str] = []
        for model in genai.list_models():
            name = (getattr(model, "name", "") or "").replace("models/", "")
            if _keep_catalog_id(name):
                out.append(name)
        return out
    except Exception as exc:
        logger.warning("Gemini model catalog failed: %s", exc)
        return []


def list_anthropic_model_ids() -> List[str]:
    """Anthropic listing is not in Bigas deps — known current ids plus defaults."""
    known = [
        "claude-fable-5-1",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-opus-4-20250514",
        "claude-sonnet-4-20250514",
    ]
    extra = [model_id for provider, model_id in DEFAULT_PRO_MODELS if provider == "anthropic"]
    seen: Set[str] = set()
    out: List[str] = []
    for model_id in extra + known:
        if model_id in seen:
            continue
        seen.add(model_id)
        out.append(model_id)
    return out


def discover_pro_models() -> List[ModelCandidate]:
    """Current flagship reasoning models from official overview pages (1–2 per provider)."""
    from bigas.eval.discover import discover_flagship_models

    return discover_flagship_models()


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


def parse_model_ref(raw: str) -> Optional[ModelCandidate]:
    text = (raw or "").strip()
    if not text:
        return None
    if ":" in text:
        provider, model_id = text.split(":", 1)
        provider = provider.strip().lower()
        model_id = model_id.strip()
        if not provider or not model_id:
            return None
        return ModelCandidate(provider, model_id)
    return ModelCandidate(_infer_provider(text), text)


def resolve_baseline_model(pack_baseline: Optional[str] = None) -> Optional[ModelCandidate]:
    """EVAL_BASELINE_MODEL wins over the pack's baseline_model."""
    env = (os.environ.get("EVAL_BASELINE_MODEL") or "").strip()
    return parse_model_ref(env or (pack_baseline or ""))


def get_candidate_models(
    use_case: str,
    *,
    storage: Optional[EvalStorage] = None,
    explicit_models: Optional[List[str]] = None,
    baseline_model: Optional[str] = None,
    include_baseline: bool = True,
) -> List[ModelCandidate]:
    """
    Return models to benchmark: production baseline + reigning champion +
    newly discovered pro models. Skips previously eliminated models, except
    the current production baseline which is always re-tested.
    """
    baseline = resolve_baseline_model(baseline_model) if include_baseline else None

    if explicit_models:
        out: List[ModelCandidate] = []
        seen: Set[str] = set()
        for raw in explicit_models:
            model = parse_model_ref(raw)
            if not model or model.key in seen:
                continue
            seen.add(model.key)
            out.append(model)
        if baseline and baseline.key not in seen:
            out.insert(0, baseline)
        return out

    state = load_eval_state(storage)
    uc_state = (state.get("use_cases") or {}).get(use_case) or {}
    champion = (uc_state.get("champion") or "").strip()
    eliminated: Set[str] = set(uc_state.get("eliminated") or [])

    discovered = discover_pro_models()
    by_id: Dict[str, ModelCandidate] = {m.model_id: m for m in discovered}
    already = set(eliminated)
    if champion:
        already.add(champion)

    candidates: List[ModelCandidate] = []
    seen: Set[str] = set()

    def _add(model: ModelCandidate, *, force: bool = False) -> None:
        if model.key in seen:
            return
        if not force and model.key in eliminated:
            return
        seen.add(model.key)
        candidates.append(model)

    if baseline:
        _add(baseline, force=True)
        already.add(baseline.key)

    if champion:
        if ":" not in champion and champion in by_id:
            champion_model = by_id[champion]
        else:
            champion_model = parse_model_ref(champion)
        if champion_model is not None:
            _add(champion_model)

    new_models = [model for model in discovered if model.key not in already]
    skipped = [model.key for model in discovered if model.key in eliminated]
    if skipped:
        logger.info("Eval skipping already-run models: %s", ", ".join(skipped))
    if new_models:
        logger.info("Eval new flagship models: %s", ", ".join(m.key for m in new_models))
    if baseline:
        logger.info("Eval including production baseline: %s", baseline.key)

    for model in new_models:
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
