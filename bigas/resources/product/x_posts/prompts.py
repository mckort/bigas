"""Prompts for weekly X post drafts."""
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

X_POSTS_SYSTEM_PROMPT = """You are a product marketer writing public updates for X (Twitter).
You evaluate a week's shipping activity and decide whether it is worth posting.

Rules:
- Draft a post when there is user-facing shipping: new features, billing/signup, UI the customer sees, analysis quality, or meaningful product improvements.
- Subjects that start with Fix/Harden/Align can still be newsworthy if they describe user-visible behavior.
- Discard only true internals: CI, lockfiles, typo-only commits, refactors with no user impact, and comments about omitted autofix.
- Autofix/automation commits have already been removed from the list. Do not skip just because stats mention autofix_omitted.
- Skip only when the remaining commit list is empty or clearly all internal.
- Do not invent features or metrics.
- Do not include Jira keys, commit SHAs, PR numbers, or personal names.
- Name the product in the tweet when it is provided.
- Each tweet must be at most 280 characters.
- Prefer one tweet. Use a short thread only when a single tweet cannot cover the user-facing news.
- Maximum 5 tweets in a thread.
- Return ONLY valid JSON matching the requested schema.
"""

_PRODUCT_NAMES = {
    "VFA": "VC Field Assistant",
    "BIG": "Bigas",
    "WAYW": "Roadpal",
    "REM": "Remotebrief",
    "GPWW": "Green Promo Wear",
    "FYDA": "Fulfil Your Dream Adventure",
    "MYL": "My Life's Deed",
}


def product_label_for_project_keys(project_keys: Optional[Sequence[str]] = None) -> str:
    keys = [str(k).strip().upper() for k in (project_keys or []) if str(k).strip()]
    labels = [_PRODUCT_NAMES.get(k, k) for k in keys]
    if not labels:
        return "the product"
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def build_x_posts_user_prompt(
    *,
    days: int,
    git_commits_text: str,
    git_stats: dict,
    product_label: str = "the product",
) -> str:
    product = (product_label or "the product").strip() or "the product"
    stats: Dict[str, Any] = git_stats if isinstance(git_stats, dict) else {}
    return f"""Evaluate git activity from the last {days} days and draft an X update for {product}'s community.

Autofix/automation commits are omitted from the list below. Git stats (JSON):
{stats}

User-facing (non-autofix) commits on default branches:
{git_commits_text}

Return JSON with this schema:
{{
  "skip": false,
  "reason": "string — required when skip is true; why this week is not worth posting",
  "tweets": ["string (<= 280 chars)", "..."]
}}

Set skip=true only if the remaining commits are empty or clearly all internal (CI, lockfiles, typo-only, no user impact).
If there is any user-facing shipping (features, billing, signup, UI, analysis quality, workflow), set skip=false and write 1–5 tweets. Mention {product}. Clear professional voice. No hashtag stuffing.
"""
