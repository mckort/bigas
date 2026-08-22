"""Shared helpers for formatting Jira comments into AI context."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from bigas.resources.product.create_release_notes.jira_client import adf_to_plain_text
from bigas.resources.product.jira_automation.config import BIGAS_COMMENT_MARKER


def format_human_comments(
    comments: List[Dict[str, Any]],
    *,
    max_comments: int = 20,
    max_chars_each: int = 1500,
) -> str:
    """
    Format Jira comments for LLM prompts.
    Skips empty comments and Bigas system comments (marker in body).
    """
    lines: List[str] = []
    # Jira returns oldest-first; prefer newest for follow-ups
    ordered = list(reversed(comments or []))
    for c in ordered:
        body = adf_to_plain_text(c.get("body")).strip()
        if not body:
            continue
        if BIGAS_COMMENT_MARKER in body or "[bigas-" in body:
            continue
        author = ((c.get("author") or {}).get("displayName") or "Unknown").strip()
        created = (c.get("created") or "")[:19].replace("T", " ")
        clipped = body if len(body) <= max_chars_each else body[: max_chars_each - 3] + "..."
        lines.append(f"- [{created}] {author}:\n  {clipped}")
        if len(lines) >= max_comments:
            break
    if not lines:
        return "(none)"
    # Show oldest of the selected window first for reading order
    return "\n".join(reversed(lines))


def issue_discord_label(issue_key: str, summary: Optional[str] = None) -> str:
    """Format `KEY` — Summary for Discord (summary optional)."""
    key = (issue_key or "").strip() or "?"
    title = (summary or "").strip()
    if title:
        return f"`{key}` — {title}"
    return f"`{key}`"
