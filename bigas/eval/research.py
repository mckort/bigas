"""Bigas-side web research for eval pack steps (not a product API)."""
from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Mapping, Sequence
from urllib.parse import urlparse

import requests

from bigas.eval.pack import PackResearch, interpolate

logger = logging.getLogger(__name__)

_DEFAULT_MAX_PAGE_CHARS = 12_000
_DEFAULT_SNIPPET_CHARS = 1_200


def fetch_page_text(url: str, *, max_chars: int = _DEFAULT_MAX_PAGE_CHARS) -> str:
    """Fetch a public URL and return visible text. Empty string on failure."""
    target = (url or "").strip()
    if not target:
        return ""
    try:
        resp = requests.get(
            target,
            timeout=20,
            headers={"User-Agent": "BigasEval/1.0 (+https://bigas.me)"},
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Eval page fetch failed for %s: %s", target, exc)
        return ""
    return html_to_text(resp.text, max_chars=max_chars)


def html_to_text(html: str, *, max_chars: int = _DEFAULT_MAX_PAGE_CHARS) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        text = re.sub(r"<script[\s\S]*?</script>", " ", html or "", flags=re.I)
        text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:max_chars]


def run_web_research(
    spec: PackResearch,
    context: Mapping[str, Any],
    *,
    extra_urls: Sequence[str] = (),
) -> str:
    """
    Level-B research: fixture page is already in context; this adds search
    snippets and optional follow-up page fetches.

    Without a search key, returns an empty snippet block (fixture page still
    lives on ``{{fixture.page}}``).
    """
    provider = (spec.provider or "web").strip().lower()
    if provider not in {"web", "tavily"}:
        raise ValueError(
            f"Unsupported research.provider {spec.provider!r}. "
            "v1 supports web (optional Tavily search)."
        )

    queries = [interpolate(q, context).strip() for q in spec.queries]
    queries = [q for q in queries if q]
    snippets: List[str] = []
    hit_urls: List[str] = []

    for query in queries:
        hits = web_search(query, max_results=spec.max_results)
        if not hits:
            continue
        snippets.append(f"QUERY: {query}")
        for hit in hits:
            url = (hit.get("url") or "").strip()
            title = (hit.get("title") or "").strip()
            content = (hit.get("content") or "").strip()
            if url:
                hit_urls.append(url)
            block = "\n".join(
                part
                for part in (
                    f"TITLE: {title}" if title else "",
                    f"URL: {url}" if url else "",
                    content[:_DEFAULT_SNIPPET_CHARS],
                )
                if part
            )
            if block:
                snippets.append(block)

    if spec.fetch_urls_from == "snippets":
        seen = set()
        fetched = 0
        for url in list(extra_urls) + hit_urls:
            if fetched >= spec.max_pages:
                break
            normalized = url.strip()
            if not normalized or normalized in seen:
                continue
            if not _is_http_url(normalized):
                continue
            seen.add(normalized)
            page = fetch_page_text(normalized, max_chars=8_000)
            if not page:
                continue
            snippets.append(f"PAGE: {normalized}\n{page}")
            fetched += 1

    if queries and not snippets:
        logger.info(
            "Eval web research produced no snippets (search key missing or empty results)"
        )
    return "\n\n---\n\n".join(snippets)


def web_search(query: str, *, max_results: int = 5) -> List[Dict[str, str]]:
    key = _search_api_key()
    if not key:
        return []
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": key,
                "query": query,
                "max_results": max(1, min(int(max_results), 8)),
                "include_raw_content": False,
                "search_depth": "basic",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Eval web search failed for %r: %s", query, exc)
        return []
    out: List[Dict[str, str]] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "content": str(item.get("content") or item.get("snippet") or ""),
            }
        )
    return out


def _search_api_key() -> str:
    return (
        (os.environ.get("EVAL_TAVILY_API_KEY") or "").strip()
        or (os.environ.get("TAVILY_API_KEY") or "").strip()
    )


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
