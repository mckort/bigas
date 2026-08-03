"""
GitHub API client for posting or updating a single PR review comment.
Uses a hidden HTML comment marker so we update the same comment on repeated runs.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

BIGAS_REVIEW_MARKER = "<!-- bigas-ai-review-marker -->"
BIGAS_AUTOFIX_COOLDOWN_MARKER = "<!-- bigas-autofix-cooldown-marker -->"


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
        action = "updated" if existing_id else "created"
        url = data.get("html_url", "")
        logger.info(
            "GitHub PR comment %s: %s#%s -> %s",
            action,
            f"{owner}/{repo}",
            pr_number,
            url,
        )
        return data

    def delete_marked_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        marker: str,
    ) -> bool:
        """
        Delete the PR comment that contains the marker. Returns True if deleted.
        """
        comment = self.get_marked_comment(owner, repo, pr_number, marker=marker)
        if not comment:
            return False
        comment_id = comment.get("id")
        if not comment_id:
            return False
        delete_url = f"https://api.github.com/repos/{owner}/{repo}/issues/comments/{comment_id}"
        resp = requests.delete(delete_url, headers=self._headers, timeout=30)
        if resp.status_code == 204:
            logger.info(
                "Deleted PR comment %s on %s/%s#%s",
                comment_id,
                owner,
                repo,
                pr_number,
            )
            return True
        if resp.status_code in (401, 403, 404):
            logger.warning(
                "Could not delete PR comment %s: HTTP %s",
                comment_id,
                resp.status_code,
            )
            return False
        resp.raise_for_status()
        return False

    def get_marked_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        marker: str = BIGAS_REVIEW_MARKER,
    ) -> dict | None:
        """Return the PR comment dict that contains marker, or None."""
        comments_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
        page = 1
        per_page = 100
        while True:
            try:
                resp = requests.get(
                    comments_url,
                    headers=self._headers,
                    params={"per_page": per_page, "page": page},
                    timeout=30,
                )
            except requests.exceptions.RequestException as e:
                raise GitHubPRCommentError(f"GitHub API request failed: {e}") from e
            if resp.status_code == 404:
                raise GitHubPRCommentError(
                    f"Repository or PR not found: {owner}/{repo}#{pr_number}."
                )
            if resp.status_code == 401:
                raise GitHubPRCommentError("GitHub token is invalid or expired.")
            if resp.status_code == 403:
                raise GitHubPRCommentError(
                    "GitHub returned 403. Check token scopes and rate limits."
                )
            try:
                resp.raise_for_status()
            except requests.exceptions.HTTPError as e:
                raise GitHubPRCommentError(
                    f"GitHub API error {resp.status_code}: {resp.text[:300]}"
                ) from e
            comments = resp.json() if resp.text else []
            if not isinstance(comments, list):
                return None
            for c in comments:
                if not isinstance(c, dict):
                    continue
                body = c.get("body") or ""
                if marker in body:
                    return c
            if len(comments) < per_page:
                break
            page += 1
        return None

    def get_marked_comment_body(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        marker: str = BIGAS_REVIEW_MARKER,
    ) -> str | None:
        """Return the body of the PR comment that contains marker, or None."""
        comment = self.get_marked_comment(owner, repo, pr_number, marker=marker)
        if not comment:
            return None
        body = comment.get("body") or ""
        return body if isinstance(body, str) else None

    def get_pr_head_commit(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> tuple[str, str]:
        """
        Return (head_sha, commit_message) for the PR head.
        """
        sha, message, _committed_at = self.get_pr_head_commit_meta(
            owner, repo, pr_number
        )
        return sha, message

    def get_pr_head_commit_meta(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> tuple[str, str, Optional[str]]:
        """
        Return (head_sha, commit_message, committed_at_iso) for the PR head.

        ``committed_at_iso`` is the committer date in ISO-8601 when available.
        """
        pr_api = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
        resp = requests.get(pr_api, headers=self._headers, timeout=30)
        if resp.status_code == 404:
            raise GitHubPRCommentError(
                f"Repository or PR not found: {owner}/{repo}#{pr_number}."
            )
        if resp.status_code == 401:
            raise GitHubPRCommentError("GitHub token is invalid or expired.")
        if resp.status_code == 403:
            raise GitHubPRCommentError(
                "GitHub returned 403. Check token scopes and rate limits."
            )
        resp.raise_for_status()
        data = resp.json() if resp.text else {}
        head = data.get("head") or {}
        sha = (head.get("sha") or "").strip()
        if not sha:
            raise GitHubPRCommentError(
                f"Could not resolve head SHA for {owner}/{repo}#{pr_number}."
            )

        commit_api = f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}"
        cresp = requests.get(commit_api, headers=self._headers, timeout=30)
        if cresp.status_code >= 400:
            logger.warning(
                "Failed to fetch commit message for %s: %s",
                sha[:8],
                cresp.status_code,
            )
            return sha, "", None
        cdata = cresp.json() if cresp.text else {}
        commit = cdata.get("commit") or {}
        message = (commit.get("message") or "").strip()
        committed_at = (
            ((commit.get("committer") or {}).get("date") or "").strip()
            or ((commit.get("author") or {}).get("date") or "").strip()
            or None
        )
        return sha, message, committed_at
    def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Fetch the pull request diff text via the GitHub API."""
        pr_api = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
        headers = {
            **self._headers,
            "Accept": "application/vnd.github.diff",
        }
        resp = requests.get(pr_api, headers=headers, timeout=60)
        if resp.status_code == 404:
            raise GitHubPRCommentError(
                f"Repository or PR not found: {owner}/{repo}#{pr_number}."
            )
        if resp.status_code == 401:
            raise GitHubPRCommentError("GitHub token is invalid or expired.")
        if resp.status_code == 403:
            raise GitHubPRCommentError(
                "GitHub returned 403. Check token scopes and rate limits."
            )
        if resp.status_code >= 400:
            raise GitHubPRCommentError(
                f"GitHub API error {resp.status_code}: {resp.text[:300]}"
            )
        return resp.text or ""

    def list_pr_commit_messages(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        max_commits: int = 250,
    ) -> list[str]:
        """Return commit messages on the PR (newest last), paginated."""
        messages: list[str] = []
        page = 1
        per_page = 100
        while len(messages) < max_commits:
            url = (
                f"https://api.github.com/repos/{owner}/{repo}/pulls/"
                f"{pr_number}/commits"
            )
            resp = requests.get(
                url,
                headers=self._headers,
                params={"per_page": per_page, "page": page},
                timeout=30,
            )
            if resp.status_code == 404:
                raise GitHubPRCommentError(
                    f"Repository or PR not found: {owner}/{repo}#{pr_number}."
                )
            if resp.status_code == 401:
                raise GitHubPRCommentError("GitHub token is invalid or expired.")
            if resp.status_code == 403:
                raise GitHubPRCommentError(
                    "GitHub returned 403. Check token scopes and rate limits."
                )
            resp.raise_for_status()
            batch = resp.json() if resp.text else []
            if not isinstance(batch, list) or not batch:
                break
            for item in batch:
                msg = ((item.get("commit") or {}).get("message") or "").strip()
                messages.append(msg)
                if len(messages) >= max_commits:
                    break
            if len(batch) < per_page:
                break
            page += 1
        return messages

    def count_autofix_commits(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        marker: str = "[bigas-autofix]",
    ) -> int:
        """Count PR commits whose message contains the autofix marker."""
        messages = self.list_pr_commit_messages(owner, repo, pr_number)
        return sum(1 for m in messages if marker in m)