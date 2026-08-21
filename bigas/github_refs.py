"""Parse GitHub PR URLs and Cursor agent links from free text."""
from __future__ import annotations

import re
from typing import Any, Optional, Tuple

GITHUB_PR_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)",
    re.IGNORECASE,
)
CURSOR_AGENT_URL_RE = re.compile(
    r"https?://(?:www\.)?cursor\.com/agents/(bc-[0-9a-f-]+)",
    re.IGNORECASE,
)
CURSOR_AGENT_ID_RE = re.compile(
    r"\b(bc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)


def format_pr_discord_line(pr_url: str, title: str = "") -> str:
    """Discord/activity-feed line for a PR. Title becomes a markdown link when present."""
    url = (pr_url or "").strip()
    cleaned = " ".join((title or "").split())
    if cleaned and url:
        # Square brackets would break markdown links; keep the title readable.
        safe = cleaned.replace("[", "(").replace("]", ")")
        return f"[{safe}]({url})"
    if url:
        return f"PR: {url}"
    return cleaned


def is_owner_repo(value: Optional[str]) -> bool:
    repo = (value or "").strip()
    if repo.count("/") != 1:
        return False
    owner, name = repo.split("/", 1)
    return bool(owner and name and "github.com" not in repo.lower())


def parse_github_pr(text: str) -> Optional[Tuple[str, int]]:
    """Return (owner/repo, pr_number) from a GitHub pull URL, or None."""
    match = GITHUB_PR_RE.search(text or "")
    if not match:
        return None
    owner, repo, number = match.group(1), match.group(2), int(match.group(3))
    if not owner or not repo or number < 1:
        return None
    return f"{owner}/{repo}", number


def parse_cursor_agent_id(text: str) -> Optional[str]:
    """Return a Cursor cloud agent id (bc-...) from a URL or bare id."""
    blob = text or ""
    match = CURSOR_AGENT_URL_RE.search(blob)
    if match:
        return match.group(1)
    match = CURSOR_AGENT_ID_RE.search(blob)
    if match:
        return match.group(1)
    return None


def coerce_pr_number(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None


def resolve_repo_and_pr(
    *,
    repo: Optional[str] = None,
    pr_number: Any = None,
    text: str = "",
) -> Tuple[str, Optional[int]]:
    """Fill owner/repo and PR number from explicit fields or a GitHub URL in text."""
    repo_out = (repo or "").strip()
    pr_out = coerce_pr_number(pr_number)
    parsed = parse_github_pr(" ".join(part for part in (text, repo_out) if part))
    if parsed:
        parsed_repo, parsed_pr = parsed
        if not is_owner_repo(repo_out):
            repo_out = parsed_repo
        if pr_out is None:
            pr_out = parsed_pr
    return repo_out, pr_out
