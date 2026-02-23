"""
GitHub API client for posting or updating a single PR review comment.
Uses a hidden HTML comment marker so we update the same comment on repeated runs.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

BIGAS_REVIEW_MARKER = "<!-- bigas-ai-review-marker -->"


class GitHubPRCommentError(RuntimeError):
    """Raised when GitHub API calls fail (auth, rate limit, not found, etc.)."""
    pass


class GitHubPRCommentClient:
    """
    Post or update a single PR comment identified by a marker.
    Uses Bearer token (fine-grained PAT or classic PAT with repo scope).
    """

    def __init__(self, token: str) -> None:
        if not (token and token.strip()):
            raise ValueError("GitHub token is required")
        self._token = token.strip()
        self._headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def post_or_update_pr_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        marker: str = BIGAS_REVIEW_MARKER,
    ) -> dict[str, Any]:
        """
        Post a new PR comment or update the existing one that contains the marker.
        Returns the API response (includes html_url, id, etc.).
        """
        comments_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
        payload_body = f"{body}\n\n{marker}"

        # List existing comments
        resp = requests.get(comments_url, headers=self._headers, timeout=30)
        if resp.status_code == 404:
            raise GitHubPRCommentError(
                f"Repository or PR not found: {owner}/{repo}#{pr_number}. "
                "Check repo name and that the token has repo scope."
            )
        if resp.status_code == 401:
            raise GitHubPRCommentError("GitHub token is invalid or expired.")
        if resp.status_code == 403:
            raise GitHubPRCommentError(
                "GitHub returned 403. Check token has repo scope and is not rate limited."
            )
        resp.raise_for_status()

        comments = resp.json() if resp.text else []
        if not isinstance(comments, list):
            comments = []
        existing_id = next(
            (c["id"] for c in comments if marker in (c.get("body") or "")),
            None,
        )

        payload = {"body": payload_body}

        if existing_id:
            patch_url = f"https://api.github.com/repos/{owner}/{repo}/issues/comments/{existing_id}"
            resp = requests.patch(
                patch_url, headers=self._headers, json=payload, timeout=30
            )
        else:
            resp = requests.post(
                comments_url, headers=self._headers, json=payload, timeout=30
            )

        if resp.status_code in (401, 403, 404):
            try:
                msg = resp.json().get("message", resp.text)
            except Exception:
                msg = resp.text
            raise GitHubPRCommentError(f"GitHub API error {resp.status_code}: {msg}")
        resp.raise_for_status()

        data = resp.json()
        logger.info(
            "GitHub PR comment %s: %s",
            "updated" if existing_id else "created",
            data.get("html_url", ""),
        )
        return data
