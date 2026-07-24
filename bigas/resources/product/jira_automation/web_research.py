"""Lightweight public web snippets for research (best-effort)."""

from __future__ import annotations

import logging
import re
from typing import List
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)


def fetch_web_snippets(query: str, *, max_results: int = 5, timeout_s: int = 15) -> str:
    """
    Best-effort DuckDuckGo HTML search snippets.
    Returns a short text block; empty string on failure.
    """
    q = (query or "").strip()
    if not q:
        return ""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(q)}"
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": "bigas-jira-automation/1.0 (+research)",
            },
            timeout=timeout_s,
        )
        if resp.status_code >= 400:
            return ""
        html = resp.text or ""
    except Exception as e:
        logger.warning("Web search failed: %s", e)
        return ""

    # Very small HTML scrape without requiring BeautifulSoup availability
    results: List[str] = []
    for m in re.finditer(
        r'class="result__a"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</(?:a|td|div)',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        snippet = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if title or snippet:
            results.append(f"- {title}: {snippet}")
        if len(results) >= max_results:
            break

    if not results:
        # Fallback: grab any result__snippet blocks
        for m in re.finditer(
            r'class="result__snippet"[^>]*>(.*?)</',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            snippet = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if snippet:
                results.append(f"- {snippet}")
            if len(results) >= max_results:
                break

    if not results:
        return ""
    return f"Web search query: {q}\n" + "\n".join(results)
