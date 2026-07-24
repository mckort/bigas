"""Fetch lightweight GitHub repo context for research prompts."""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class GitHubContextError(RuntimeError):
    pass


class GitHubRepoContext:
    def __init__(self, token: Optional[str] = None, *, timeout_s: int = 30):
        self._token = (token or os.environ.get("GITHUB_TOKEN") or "").strip()
        self._timeout_s = timeout_s
        self._session = requests.Session()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "bigas-jira-automation",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        self._session.headers.update(headers)

    def fetch_context(self, repo: str, *, query_hints: Optional[List[str]] = None) -> str:
        """
        Return a text bundle: README excerpt + top-level tree + optional code search hits.
        Soft-fails into a short note if GitHub is unavailable.
        """
        repo = (repo or "").strip().strip("/")
        if not repo or "/" not in repo:
            return "(No GitHub repo configured for this Jira project.)"

        parts: List[str] = [f"Repository: {repo}"]
        try:
            readme = self._get_readme(repo)
            if readme:
                parts.append("## README (excerpt)\n" + readme[:6000])
        except Exception as e:
            logger.warning("GitHub README fetch failed for %s: %s", repo, e)
            parts.append(f"(README unavailable: {e})")

        try:
            tree = self._get_top_level_paths(repo)
            if tree:
                parts.append("## Top-level paths\n" + "\n".join(f"- {p}" for p in tree[:80]))
        except Exception as e:
            logger.warning("GitHub tree fetch failed for %s: %s", repo, e)

        hints = [h.strip() for h in (query_hints or []) if (h or "").strip()]
        if hints and self._token:
            try:
                hits = self._code_search(repo, hints[:3])
                if hits:
                    parts.append("## Code search hits\n" + "\n".join(hits))
            except Exception as e:
                logger.warning("GitHub code search failed for %s: %s", repo, e)

        if not self._token:
            parts.append(
                "(GITHUB_TOKEN not set — private repos and code search may be unavailable.)"
            )
        return "\n\n".join(parts)

    def _get_readme(self, repo: str) -> str:
        url = f"https://api.github.com/repos/{repo}/readme"
        resp = self._session.get(url, timeout=self._timeout_s)
        if resp.status_code == 404:
            return ""
        if resp.status_code >= 400:
            raise GitHubContextError(f"readme {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        content = data.get("content") or ""
        encoding = (data.get("encoding") or "base64").lower()
        if encoding == "base64":
            raw = base64.b64decode(content).decode("utf-8", errors="replace")
        else:
            raw = str(content)
        return raw.strip()

    def _get_default_branch(self, repo: str) -> str:
        url = f"https://api.github.com/repos/{repo}"
        resp = self._session.get(url, timeout=self._timeout_s)
        if resp.status_code >= 400:
            return "main"
        return (resp.json().get("default_branch") or "main").strip() or "main"

    def _get_top_level_paths(self, repo: str) -> List[str]:
        branch = self._get_default_branch(repo)
        url = f"https://api.github.com/repos/{repo}/git/trees/{branch}"
        resp = self._session.get(url, params={"recursive": "0"}, timeout=self._timeout_s)
        if resp.status_code >= 400:
            # try without recursive / alternate
            raise GitHubContextError(f"tree {resp.status_code}: {resp.text[:200]}")
        tree = resp.json().get("tree") or []
        paths = []
        for item in tree:
            path = item.get("path") or ""
            if path and "/" not in path:
                kind = item.get("type") or ""
                paths.append(f"{path}/" if kind == "tree" else path)
        return sorted(paths)

    def _code_search(self, repo: str, hints: List[str]) -> List[str]:
        lines: List[str] = []
        for hint in hints:
            q = f"{hint} repo:{repo}"
            resp = self._session.get(
                "https://api.github.com/search/code",
                params={"q": q, "per_page": 5},
                timeout=self._timeout_s,
            )
            if resp.status_code >= 400:
                continue
            items = resp.json().get("items") or []
            for it in items[:5]:
                path = it.get("path") or ""
                html = it.get("html_url") or ""
                lines.append(f"- `{path}` ({html})")
        return lines[:15]
