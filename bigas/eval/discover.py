"""Find the current top reasoning models (1–2 per provider) from official docs."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from bigas.eval.registry import DEFAULT_PRO_MODELS, ModelCandidate

logger = logging.getLogger(__name__)

PROVIDERS = ("openai", "anthropic", "gemini")

# Vendor model-overview pages. Paths stay stable; the lineup on the page changes.
OFFICIAL_MODEL_PAGES: Dict[str, str] = {
    "openai": "https://developers.openai.com/api/docs/models",
    "anthropic": "https://platform.claude.com/docs/en/models/overview",
    "gemini": "https://ai.google.dev/gemini-api/docs/models",
}

_NOT_REASONING_PARTS = {
    "embed",
    "embedding",
    "whisper",
    "tts",
    "dall",
    "realtime",
    "audio",
    "transcribe",
    "moderation",
    "instruct",
    "mini",
    "nano",
    "lite",
    "flash",
    "image",
    "sora",
    "lyria",
    "veo",
    "imagen",
    "codex",
    "search",
    "haiku",
    "luna",
    "daybreak",
    "cyber",
}
_NOT_REASONING_COMPOUNDS = ("deep-research", "computer-use", "dall-e")
_DATED_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_ID_PATTERNS: Dict[str, re.Pattern[str]] = {
    "openai": re.compile(r"\b((?:gpt|o\d)(?:-[a-z0-9.]+)+)\b", re.I),
    "anthropic": re.compile(r"\b(claude-(?:fable|opus|sonnet)(?:-[a-z0-9.]+)+)\b", re.I),
    "gemini": re.compile(r"\b(gemini-(?:[a-z0-9.]+-)*pro(?:-[a-z0-9.]+)*)\b", re.I),
}


def max_models_per_provider() -> int:
    raw = (os.environ.get("EVAL_MODELS_PER_PROVIDER") or "2").strip()
    try:
        return max(1, min(int(raw), 2))
    except ValueError:
        return 2


def discover_flagship_models(
    *,
    catalog: Optional[Mapping[str, Sequence[str]]] = None,
    snippets: Optional[str] = None,
    picked: Optional[Mapping[str, Sequence[str]]] = None,
) -> List[ModelCandidate]:
    """
    Return at most two current reasoning models per provider.

    Primary source is each vendor's model-overview page. Live catalogs are
    used to confirm ids. Falls back to DEFAULT_PRO_MODELS if a page is down.
    """
    resolved_catalog = dict(catalog or load_provider_catalogs())
    text = snippets if snippets is not None else gather_flagship_snippets()
    names = dict(picked or pick_flagship_names(text))
    cap = max_models_per_provider()
    out: List[ModelCandidate] = []
    for provider in PROVIDERS:
        wanted = list(names.get(provider) or [])
        if not wanted:
            wanted = extract_ids_from_text(provider, text)
        ids = resolve_against_catalog(
            provider,
            wanted,
            resolved_catalog.get(provider) or (),
        )
        if not ids:
            ids = [item for item in wanted if item and not is_not_reasoning(item)][:cap]
        if not ids:
            ids = fallback_ids(provider, resolved_catalog.get(provider) or (), cap)
            if ids:
                logger.info("Official model page missed %s; using fallback %s", provider, ids)
        for model_id in ids[:cap]:
            out.append(ModelCandidate(provider, model_id))
    return out


def gather_flagship_snippets() -> str:
    from bigas.eval.research import fetch_page_text, web_search

    blocks: List[str] = []
    for provider, url in OFFICIAL_MODEL_PAGES.items():
        page = fetch_page_text(url, max_chars=10_000)
        if page:
            blocks.append(f"PROVIDER: {provider}\nPAGE: {url}\n{page}")
            continue
        logger.info("Official model page empty for %s; trying search fallback", provider)
        for hit in web_search(f"{provider} flagship reasoning model site:{url}", max_results=3):
            title = (hit.get("title") or "").strip()
            content = (hit.get("content") or "").strip()
            if title or content:
                blocks.append(
                    f"PROVIDER: {provider}\nTITLE: {title}\nURL: {hit.get('url') or url}\n{content[:1200]}"
                )
    if not blocks:
        logger.info("Flagship discovery produced no official-page text")
    return "\n\n---\n\n".join(blocks)


def pick_flagship_names(snippets: str) -> Dict[str, List[str]]:
    """Ask a small LLM to extract at most two reasoning model ids per provider."""
    empty = {provider: [] for provider in PROVIDERS}
    if not (snippets or "").strip():
        return empty
    prompt = (
        "From the official model-overview pages below, pick today's top reasoning "
        "chat models for an investor-analysis eval.\n"
        "One model per provider, two only if the page names a clear #1 and #2 "
        "(e.g. Claude Fable + Opus, or GPT Astra + GPT-5.6 Sol).\n"
        "Return JSON only:\n"
        '{"openai":["<api-id>"],"anthropic":["<api-id>"],"gemini":["<api-id>"]}\n'
        "Use the Claude API ID / Model ID strings on the page. "
        "Skip images, video, audio, embeddings, search-only, cyber, haiku, mini, "
        "flash, luna, and legacy tables.\n\n"
        f"Sources:\n{snippets[:14000]}"
    )
    try:
        from bigas.llm.factory import get_llm_client

        client, _model = get_llm_client(feature="model_eval_judge")
        raw = client.complete(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=400,
        )
        text = raw if isinstance(raw, str) else getattr(raw, "text", "") or ""
        parsed = _extract_json(text)
    except Exception as exc:
        logger.warning("Flagship name parse failed: %s", exc)
        return {provider: extract_ids_from_text(provider, snippets) for provider in PROVIDERS}
    if not parsed:
        return {provider: extract_ids_from_text(provider, snippets) for provider in PROVIDERS}
    out: Dict[str, List[str]] = {}
    for provider in PROVIDERS:
        values = parsed.get(provider) or []
        if isinstance(values, str):
            values = [values]
        cleaned: List[str] = []
        for item in values:
            model_id = normalize_model_id(str(item))
            if model_id and not is_not_reasoning(model_id):
                cleaned.append(model_id)
        if not cleaned:
            cleaned = extract_ids_from_text(provider, snippets)
        out[provider] = cleaned[:2]
    return out


def extract_ids_from_text(provider: str, text: str) -> List[str]:
    pattern = _ID_PATTERNS.get(provider)
    if not pattern or not text:
        return []
    found: List[str] = []
    seen: set[str] = set()
    for match in pattern.findall(text):
        model_id = normalize_model_id(match)
        if not model_id or model_id in seen or is_not_reasoning(model_id):
            continue
        seen.add(model_id)
        found.append(model_id)
    return found[:2]


def resolve_against_catalog(
    provider: str,
    wanted: Sequence[str],
    catalog: Sequence[str],
) -> List[str]:
    catalog_list = [normalize_model_id(x) for x in catalog if str(x).strip()]
    if not catalog_list:
        return [normalize_model_id(x) for x in wanted if x and not is_not_reasoning(x)]
    chosen: List[str] = []
    seen: set[str] = set()
    for raw in wanted:
        match = match_catalog_id(normalize_model_id(raw), catalog_list)
        if not match or match in seen or is_not_reasoning(match):
            continue
        seen.add(match)
        chosen.append(match)
    return chosen


def match_catalog_id(wanted: str, catalog: Sequence[str]) -> Optional[str]:
    if not wanted:
        return None
    if wanted in catalog:
        return wanted
    prefix_hits = [
        item
        for item in catalog
        if item == wanted or item.startswith(wanted + "-") or wanted.startswith(item + "-")
    ]
    if not prefix_hits:
        return None
    undated = [item for item in prefix_hits if not _DATED_SUFFIX.search(item)]
    pool = undated or prefix_hits
    return sorted(pool, key=len)[0]


def fallback_ids(provider: str, catalog: Sequence[str], cap: int) -> List[str]:
    defaults = [model_id for prov, model_id in DEFAULT_PRO_MODELS if prov == provider]
    if catalog:
        resolved = resolve_against_catalog(provider, defaults, catalog)
        if resolved:
            return resolved[:cap]
    return defaults[:cap]


def is_not_reasoning(model_id: str) -> bool:
    """True for image/audio/search/lite ids. Matches hyphen parts, not 'mini' in 'gemini'."""
    name = (model_id or "").strip().lower()
    if not name:
        return True
    if any(token in name for token in _NOT_REASONING_COMPOUNDS):
        return True
    parts = set(re.split(r"[-_.]+", name))
    return bool(parts & _NOT_REASONING_PARTS)


def normalize_model_id(raw: str) -> str:
    value = (raw or "").strip().lower().replace("models/", "")
    value = value.replace(" ", "-")
    return re.sub(r"[^a-z0-9._:-]+", "-", value).strip("-")


def load_provider_catalogs() -> Dict[str, List[str]]:
    from bigas.eval.registry import list_anthropic_model_ids, list_gemini_model_ids, list_openai_model_ids

    return {
        "openai": list_openai_model_ids(),
        "anthropic": list_anthropic_model_ids(),
        "gemini": list_gemini_model_ids(),
    }


def _extract_json(text: str) -> Optional[Dict[str, object]]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
    return None
