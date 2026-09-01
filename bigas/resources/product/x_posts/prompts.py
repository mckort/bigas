"""Prompts for weekly X post drafts."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Sequence

X_POSTS_SYSTEM_PROMPT = """You are a product marketer writing public updates for X (Twitter).
You read a week's shipped tickets (internal board, and Jira when available) plus git commits, and decide whether customers of this one product would care.

Do this in order:
1. Read the shipped tickets and the entire commit list. Do not judge the week from volume, from Fix/Harden/Align prefixes, or from the first few lines.
2. Extract customer-relevant product improvements: a change a customer would notice in the product itself (new capability, new or improved workflow, analysis quality, signup/billing they use, visible product UI). Paraphrase each in plain language.
3. Ignore internals even if they are numerous: CI, lockfiles, type pins, review tooling, layout spacing, copy nits, refactors with no user impact, comments about omitted autofix.
4. Tickets moved to Done and commits whose subject contains this product's issue key (for example VFA-32) are shipped features. Always treat those as newsworthy.
5. If the extracted list is empty and there are no shipped tickets or Jira-key feature commits, skip. If either has items, draft tweets about those. A mixed week of chores plus real product changes is still a draft week.
6. Do not invent features or metrics. Do not include Jira keys, commit SHAs, PR numbers, or personal names in the tweets.
7. Name the product in the tweet when it is provided. Write only for this product's community — do not mention other brands.
8. Each tweet must be at most 280 characters. Prefer one tweet; at most 5.
9. Return ONLY valid JSON matching the requested schema.
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
    if project_keys is None:
        project_keys = []
    elif isinstance(project_keys, str):
        project_keys = [project_keys]
    keys = list(
        dict.fromkeys(
            str(k).strip().upper()
            for k in project_keys
            if k is not None and str(k).strip()
        )
    )
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
    jira_features_text: str = "",
    done_issues_text: str = "",
) -> str:
    product = (product_label or "the product").strip() or "the product"
    stats: Dict[str, Any] = git_stats if isinstance(git_stats, dict) else {}
    jira_text = (jira_features_text or "").strip()
    done_text = (done_issues_text or "").strip()
    has_shipped = bool(jira_text or done_text)
    extra_sections = ""
    if done_text:
        extra_sections += f"""
Shipped tickets from the internal board and Jira (deduped). Treat every item as newsworthy. Do not skip.
{done_text}
"""
    if jira_text:
        extra_sections += f"""
Shipped Jira features (commit subject contains this product's issue key, e.g. VFA-12). These were created as Jira features, then developed and merged. Treat every item as newsworthy. Do not skip.
{jira_text}
"""
    if has_shipped:
        skip_rule = (
            "skip MUST be false because shipped tickets or Jira features are listed. "
            "Draft tweets covering those features (and any other customer-relevant items). "
            f"Mention {product}."
        )
    else:
        skip_rule = (
            "Fill newsworthy first. Set skip=true only if newsworthy is empty. "
            "If newsworthy is not empty, skip must be false and tweets must cover those improvements. "
            f"Mention {product}."
        )
    return f"""Draft an X update for {product}'s community from shipped tickets and git activity in the last {days} days.

Autofix/automation commits are already omitted. Git stats (JSON):
{json.dumps(stats)}
{extra_sections}
Commits on default branches (mixed product work and internals — you must filter):
{git_commits_text}

Return JSON with this schema:
{{
  "newsworthy": ["short paraphrase of each customer-relevant product improvement"],
  "skip": false,
  "reason": "string — required when skip is true; why nothing is customer-relevant",
  "tweets": ["string (<= 280 chars)", "..."]
}}

{skip_rule} Clear professional voice. No hashtag stuffing.
"""
