"""Orchestrate Cursor autofix from a Bigas PR review comment."""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from bigas.resources.cto.autofix.cursor_client import (
    CursorCloudAgentClient,
    CursorCloudAgentError,
)
from bigas.resources.cto.autofix.heuristics import (
    AUTOFIX_COMMIT_MARKER,
    latest_commit_is_autofix,
    review_needs_autofix,
)
from bigas.resources.cto.pr_review.github_client import (
    BIGAS_REVIEW_MARKER,
    GitHubPRCommentClient,
    GitHubPRCommentError,
)

logger = logging.getLogger(__name__)


class AutofixError(RuntimeError):
    pass


def _build_prompt(*, repo: str, pr_number: int, pr_url: str, review_body: str) -> str:
    return f"""You are fixing findings from an automated Bigas CTO PR review.

Repository: {repo}
Pull request: {pr_url}

## Bigas review comment
{review_body}

## Instructions
1. Fix only clear, actionable problems called out in the review (bugs, broken behavior, security, must-fix / blocking items).
2. Skip pure nits and non-blocking style suggestions unless they are trivial one-line fixes already implied by the review.
3. Do not expand scope, refactor unrelated code, or change public APIs unless required by a finding.
4. Push commits directly to this PR's head branch (already checked out for you).
5. Every commit message you create MUST include the exact marker `{AUTOFIX_COMMIT_MARKER}`.
6. Do not merge the PR, do not force-push, do not rewrite history, and do not open a new PR.
7. If after inspecting the code there is nothing safe to fix, make no commits and explain why.
8. Do NOT ask for confirmation, approval, or whether to proceed. This is an unattended cloud agent — apply the fixes and push commits immediately. Do not stop after a proposal.
"""


def autofix_looks_like_confirmation_stop(result_text: str) -> bool:
    """True when Cursor run result suggests it stopped to ask for confirmation."""
    text = (result_text or "").lower()
    if not text:
        return False
    needles = (
        "shall i proceed",
        "please confirm",
        "confirm before",
        "before i proceed",
        "want me to proceed",
        "should i proceed",
        "do you want me to",
    )
    return any(n in text for n in needles)


class AutofixService:
    def __init__(
        self,
        *,
        cursor_api_key: Optional[str] = None,
        github_token: Optional[str] = None,
        cursor_model: Optional[str] = None,
    ) -> None:
        self._cursor_key = (
            (cursor_api_key or "").strip()
            or (os.environ.get("CURSOR_API_KEY") or "").strip()
        )
        self._github_token = (
            (github_token or "").strip()
            or (os.environ.get("GITHUB_TOKEN") or "").strip()
        )
        self._cursor_model = (
            (cursor_model or "").strip()
            or (os.environ.get("BIGAS_CTO_AUTOFIX_MODEL") or "").strip()
            or None
        )
        if not self._cursor_key:
            raise AutofixError("CURSOR_API_KEY is required for autofix")
        if not self._github_token:
            raise AutofixError("GITHUB_TOKEN is required for autofix")

    def run(
        self,
        *,
        repo: str,
        pr_number: int,
        force: bool = False,
        review_body: Optional[str] = None,
    ) -> dict[str, Any]:
        owner, repo_name = repo.split("/", 1)
        pr_url = f"https://github.com/{repo}/pull/{pr_number}"
        repo_url = f"https://github.com/{repo}"

        gh = GitHubPRCommentClient(token=self._github_token)

        try:
            head_sha, head_message = gh.get_pr_head_commit(
                owner=owner, repo=repo_name, pr_number=pr_number
            )
        except GitHubPRCommentError as e:
            raise AutofixError(str(e)) from e

        if latest_commit_is_autofix(head_message) and not force:
            return {
                "skipped": True,
                "reason": f"latest commit already autofix ({head_sha[:8]})",
                "pr_url": pr_url,
            }

        body = (review_body or "").strip()
        if not body:
            try:
                found = gh.get_marked_comment_body(
                    owner=owner,
                    repo=repo_name,
                    pr_number=pr_number,
                    marker=BIGAS_REVIEW_MARKER,
                )
            except GitHubPRCommentError as e:
                raise AutofixError(str(e)) from e
            if not found:
                return {
                    "skipped": True,
                    "reason": "no Bigas review comment found on PR",
                    "pr_url": pr_url,
                }
            body = found

        if not force:
            should, reason = review_needs_autofix(body)
            if not should:
                return {
                    "skipped": True,
                    "reason": reason,
                    "pr_url": pr_url,
                }

        prompt = _build_prompt(
            repo=repo, pr_number=pr_number, pr_url=pr_url, review_body=body
        )
        client = CursorCloudAgentClient(api_key=self._cursor_key)
        try:
            launched = client.launch_pr_autofix(
                repo_url=repo_url,
                pr_url=pr_url,
                prompt_text=prompt,
                name=f"Bigas autofix {repo}#{pr_number}",
                model_id=self._cursor_model,
            )
        except CursorCloudAgentError as e:
            raise AutofixError(str(e)) from e

        return {
            "skipped": False,
            "launched": True,
            "pr_url": pr_url,
            "agent_id": launched.get("agent_id") or "",
            "agent_url": launched.get("agent_url") or "",
            "run_id": launched.get("run_id") or "",
            "forced": bool(force),
        }

    def poll_status(
        self,
        *,
        agent_id: str,
        run_id: Optional[str] = None,
    ) -> dict[str, Any]:
        client = CursorCloudAgentClient(api_key=self._cursor_key)
        try:
            return client.get_run_status(agent_id=agent_id, run_id=run_id)
        except CursorCloudAgentError as e:
            raise AutofixError(str(e)) from e

    def get_pr_head_commit(
        self, *, repo: str, pr_number: int
    ) -> tuple[str, str]:
        owner, repo_name = repo.split("/", 1)
        gh = GitHubPRCommentClient(token=self._github_token)
        try:
            return gh.get_pr_head_commit(
                owner=owner, repo=repo_name, pr_number=pr_number
            )
        except GitHubPRCommentError as e:
            raise AutofixError(str(e)) from e

    def fetch_pr_diff(self, *, repo: str, pr_number: int) -> str:
        owner, repo_name = repo.split("/", 1)
        gh = GitHubPRCommentClient(token=self._github_token)
        try:
            return gh.get_pr_diff(owner=owner, repo=repo_name, pr_number=pr_number)
        except GitHubPRCommentError as e:
            raise AutofixError(str(e)) from e
