"""Read-only GitHub commits and merged PRs for chat Q&A."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from bigas.resources.product.progress_updates.github_commits import (
    GitHubCommitsClient,
    GitHubCommitsError,
    normalize_commit,
    project_repo_map_from_env,
)

logger = logging.getLogger(__name__)


def resolve_activity_since(
    *,
    since: Optional[str] = None,
    days: Optional[int] = None,
) -> datetime:
    """Parse an ISO date or lookback window into a UTC datetime."""
    raw = (since or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.strptime(raw[:10], "%Y-%m-%d")
            except ValueError as exc:
                raise GitHubCommitsError(f"Invalid since date: {since}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    lookback = 14
    if days is not None:
        try:
            lookback = int(days)
        except (TypeError, ValueError) as exc:
            raise GitHubCommitsError("days must be an integer between 1 and 365") from exc
    if lookback < 1 or lookback > 365:
        raise GitHubCommitsError("days must be between 1 and 365")
    return datetime.now(timezone.utc) - timedelta(days=lookback)


def _parse_github_datetime(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def list_merged_pulls_since(
    client: GitHubCommitsClient,
    *,
    owner: str,
    repo: str,
    since: datetime,
    per_page: int = 100,
    max_pages: int = 3,
) -> List[Dict[str, Any]]:
    """List merged PRs whose merged_at is on or after `since` (UTC)."""
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    since_utc = since.astimezone(timezone.utc)
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    out: List[Dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        resp = requests.get(
            url,
            headers=client._headers,
            params={
                "state": "closed",
                "sort": "updated",
                "direction": "desc",
                "per_page": max(1, min(per_page, 100)),
                "page": page,
            },
            timeout=45,
        )
        if resp.status_code == 404:
            logger.warning("GitHub repo not found or inaccessible: %s/%s", owner, repo)
            break
        if resp.status_code in (401, 403):
            raise GitHubCommitsError(
                f"GitHub pulls auth/rate-limit error {resp.status_code} "
                f"for {owner}/{repo}: {resp.text[:200]}"
            )
        if resp.status_code >= 400:
            raise GitHubCommitsError(
                f"GitHub pulls error {resp.status_code} for {owner}/{repo}: "
                f"{resp.text[:300]}"
            )
        batch = resp.json() if resp.text else []
        if not isinstance(batch, list) or not batch:
            break
        recent_updates = False
        for item in batch:
            if not isinstance(item, dict):
                continue
            updated_at = _parse_github_datetime(item.get("updated_at"))
            if updated_at is None or updated_at >= since_utc:
                recent_updates = True
            merged_at = _parse_github_datetime(item.get("merged_at"))
            if merged_at is None or merged_at < since_utc:
                continue
            user = item.get("user") if isinstance(item.get("user"), dict) else {}
            out.append(
                {
                    "number": item.get("number"),
                    "title": str(item.get("title") or "").strip(),
                    "merged_at": merged_at.isoformat(),
                    "html_url": str(item.get("html_url") or "").strip(),
                    "user": str(user.get("login") or "").strip(),
                }
            )
        if not recent_updates or len(batch) < per_page:
            break
        page += 1
    return out


def fetch_github_activity(
    *,
    project_key: Optional[str] = None,
    repo: Optional[str] = None,
    since: Optional[str] = None,
    days: Optional[int] = None,
    include_prs: bool = True,
    token: Optional[str] = None,
    max_commits: int = 80,
    client: Optional[GitHubCommitsClient] = None,
) -> Dict[str, Any]:
    """Read-only GitHub commits (and merged PRs) since a date. Does not post anywhere."""
    from bigas.portfolio import normalize_project_key, repo_map as portfolio_repo_map

    mapping = project_repo_map_from_env()
    key = normalize_project_key(project_key)
    repo_name = (repo or "").strip()
    if not repo_name and key:
        repo_name = (mapping.get(key) or portfolio_repo_map().get(key) or "").strip()
    if not key and repo_name:
        for mapped_key, mapped_repo in mapping.items():
            if (mapped_repo or "").strip() == repo_name:
                key = mapped_key
                break
    if not repo_name or "/" not in repo_name:
        raise GitHubCommitsError(
            "repo (owner/repo) or a mapped project_key is required"
        )
    cutoff = resolve_activity_since(since=since, days=days)
    owner, name = repo_name.split("/", 1)
    github = client or GitHubCommitsClient(token=token)
    raw_commits = github.list_commits_since(owner=owner, repo=name, since=cutoff)
    commits: List[Dict[str, Any]] = []
    autofix = 0
    for item in raw_commits:
        normalized = normalize_commit(item, project_key=key or "", repo=repo_name)
        if normalized is None:
            continue
        if normalized.get("is_autofix"):
            autofix += 1
        if len(commits) < max_commits:
            commits.append(
                {
                    "sha": normalized.get("sha"),
                    "subject": normalized.get("subject"),
                    "committed_at": normalized.get("committed_at"),
                    "html_url": normalized.get("html_url"),
                    "is_autofix": bool(normalized.get("is_autofix")),
                }
            )
    pulls: List[Dict[str, Any]] = []
    if include_prs:
        pulls = list_merged_pulls_since(github, owner=owner, repo=name, since=cutoff)
    return {
        "ok": True,
        "project_key": key or None,
        "repo": repo_name,
        "since": cutoff.strftime("%Y-%m-%d"),
        "commits": commits,
        "pull_requests": pulls,
        "stats": {
            "commits": len(commits),
            "autofix": autofix,
            "pull_requests": len(pulls),
        },
    }
