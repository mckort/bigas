"""Fetch recent default-branch commits from GitHub for progress updates."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import requests

from bigas.resources.product.jira_automation.config import JiraAutomationConfig

logger = logging.getLogger(__name__)

_AUTOFIX_MARKER_RE = re.compile(r"\[bigas-autofix\]", re.IGNORECASE)
_MERGE_SUBJECT_RE = re.compile(r"^merge\b", re.IGNORECASE)


class GitHubCommitsError(RuntimeError):
    pass


def project_repo_map_from_env() -> Dict[str, str]:
    """Return Jira project key → owner/repo from env (with defaults)."""
    return dict(JiraAutomationConfig.from_env().project_repos)


def _is_merge_commit(commit: Dict[str, Any], message: str) -> bool:
    parents = commit.get("parents") or []
    if isinstance(parents, list) and len(parents) > 1:
        return True
    first_line = (message or "").strip().split("\n", 1)[0].strip()
    return bool(_MERGE_SUBJECT_RE.match(first_line))


def normalize_commit(raw: Dict[str, Any], *, project_key: str, repo: str) -> Optional[Dict[str, Any]]:
    """Normalize a GitHub commit payload; return None for merge noise."""
    if not isinstance(raw, dict):
        return None
    commit = raw.get("commit") or {}
    if not isinstance(commit, dict):
        commit = {}
    message = (commit.get("message") or "").strip()
    if not message:
        return None
    if _is_merge_commit(raw, message):
        return None
    subject = message.split("\n", 1)[0].strip()
    author = ""
    author_obj = commit.get("author") or {}
    if isinstance(author_obj, dict):
        author = (author_obj.get("name") or "").strip()
    sha = (raw.get("sha") or "")[:8]
    return {
        "project_key": project_key,
        "repo": repo,
        "sha": sha,
        "subject": subject,
        "message": message,
        "author": author,
        "is_autofix": bool(_AUTOFIX_MARKER_RE.search(message)),
        "html_url": (raw.get("html_url") or "").strip(),
    }


class GitHubCommitsClient:
    def __init__(self, token: Optional[str] = None) -> None:
        key = (token or "").strip() or (os.environ.get("GITHUB_TOKEN") or "").strip()
        if not key:
            raise GitHubCommitsError("GITHUB_TOKEN is required to fetch git commits")
        self._headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def list_commits_since(
        self,
        *,
        owner: str,
        repo: str,
        since: datetime,
        per_page: int = 100,
        max_pages: int = 3,
    ) -> List[Dict[str, Any]]:
        """List commits on the default branch since `since` (UTC)."""
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        since_iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"https://api.github.com/repos/{owner}/{repo}/commits"
        out: List[Dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            resp = requests.get(
                url,
                headers=self._headers,
                params={
                    "since": since_iso,
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
                    f"GitHub commits auth/rate-limit error {resp.status_code} "
                    f"for {owner}/{repo}: {resp.text[:200]}"
                )
            if resp.status_code >= 400:
                raise GitHubCommitsError(
                    f"GitHub commits error {resp.status_code} for {owner}/{repo}: "
                    f"{resp.text[:300]}"
                )
            batch = resp.json() if resp.text else []
            if not isinstance(batch, list) or not batch:
                break
            out.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < per_page:
                break
            page += 1
        return out


def fetch_commits_for_projects(
    *,
    project_keys: Sequence[str],
    days: int = 7,
    token: Optional[str] = None,
    repo_map: Optional[Dict[str, str]] = None,
    max_commits_per_repo: int = 40,
) -> Dict[str, Any]:
    """
    Fetch normalized commits for mapped repos.

    Returns:
      {
        "by_project": { "VFA": [commit, ...], ... },
        "stats": { "VFA": {"total": n, "autofix": m, "repo": "..."}, ... },
        "errors": [{"project_key", "repo", "error"}, ...],
      }
    """
    keys = [k.strip().upper() for k in project_keys if (k or "").strip()]
    mapping = repo_map if repo_map is not None else project_repo_map_from_env()
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))

    try:
        client = GitHubCommitsClient(token=token)
    except GitHubCommitsError as e:
        return {
            "by_project": {k: [] for k in keys},
            "stats": {},
            "errors": [{"project_key": "*", "repo": "", "error": str(e)}],
        }

    by_project: Dict[str, List[Dict[str, Any]]] = {k: [] for k in keys}
    stats: Dict[str, Dict[str, Any]] = {}
    errors: List[Dict[str, str]] = []

    for key in keys:
        repo = (mapping.get(key) or "").strip()
        if not repo or "/" not in repo:
            stats[key] = {"total": 0, "autofix": 0, "repo": repo or None}
            continue
        owner, repo_name = repo.split("/", 1)
        try:
            raw = client.list_commits_since(owner=owner, repo=repo_name, since=since)
        except GitHubCommitsError as e:
            logger.warning("Failed fetching commits for %s (%s): %s", key, repo, e)
            errors.append({"project_key": key, "repo": repo, "error": str(e)})
            stats[key] = {"total": 0, "autofix": 0, "repo": repo}
            continue

        normalized: List[Dict[str, Any]] = []
        for item in raw:
            n = normalize_commit(item, project_key=key, repo=repo)
            if n is not None:
                normalized.append(n)
            if len(normalized) >= max_commits_per_repo:
                break

        by_project[key] = normalized
        stats[key] = {
            "total": len(normalized),
            "autofix": sum(1 for c in normalized if c.get("is_autofix")),
            "repo": repo,
        }

    return {"by_project": by_project, "stats": stats, "errors": errors}


def format_commits_for_prompt(
    by_project: Dict[str, List[Dict[str, Any]]],
    *,
    stats: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """Human-readable commit list for the LLM prompt."""
    if not by_project:
        return "(No git commit activity fetched.)"

    lines: List[str] = []
    any_commits = False
    for project in sorted(by_project.keys()):
        commits = by_project.get(project) or []
        meta = (stats or {}).get(project) or {}
        repo = meta.get("repo") or ""
        header = f"### {project}" + (f" ({repo})" if repo else "")
        if not commits:
            lines.append(header)
            lines.append("- (no non-merge commits on default branch in this period)")
            continue
        any_commits = True
        lines.append(header)
        for c in commits:
            tag = " [autofix]" if c.get("is_autofix") else ""
            lines.append(f"- {c.get('subject', '')}{tag}")
    if not any_commits:
        return "(No non-merge git commits on default branches in this period.)"
    return "\n".join(lines)
