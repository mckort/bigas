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
    autofix_max_iterations,
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
1. Fix all Blockers and Important items called out in the review.
2. Also fix Minor items listed in the same review — they ride along in this round when Blockers/Important already triggered autofix.
3. Do not invent extra polish beyond what the review lists. Do not expand scope or refactor unrelated code.
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
        max_iters = autofix_max_iterations()

        gh = GitHubPRCommentClient(token=self._github_token)

        try:
            head_sha, head_message = gh.get_pr_head_commit(
                owner=owner, repo=repo_name, pr_number=pr_number
            )
            autofix_count = gh.count_autofix_commits(
                owner, repo_name, pr_number, marker=AUTOFIX_COMMIT_MARKER
            )
        except GitHubPRCommentError as e:
            raise AutofixError(str(e)) from e

        if autofix_count >= max_iters and not force:
            return {
                "skipped": True,
                "loop_protection": True,
                "reason": (
                    f"loop protection: {autofix_count}/{max_iters} autofix rounds "
                    f"already on this PR — remaining review comments need manual handling"
                ),
                "pr_url": pr_url,
                "autofix_count": autofix_count,
                "max_iterations": max_iters,
                "head_sha": head_sha,
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
                    "autofix_count": autofix_count,
                    "max_iterations": max_iters,
                }
            body = found

        if not force:
            should, reason = review_needs_autofix(body)
            if not should:
                return {
                    "skipped": True,
                    "reason": reason,
                    "pr_url": pr_url,
                    "autofix_count": autofix_count,
                    "max_iterations": max_iters,
                    "review_clean": True,
                }

        next_round = autofix_count + 1
        prompt = _build_prompt(
            repo=repo, pr_number=pr_number, pr_url=pr_url, review_body=body
        )
        client = CursorCloudAgentClient(api_key=self._cursor_key)
        try:
            launched = client.launch_pr_autofix(
                repo_url=repo_url,
                pr_url=pr_url,
                prompt_text=prompt,
                name=f"Bigas autofix {repo}#{pr_number} ({next_round}/{max_iters})",
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
            "autofix_count": autofix_count,
            "autofix_round": next_round,
            "max_iterations": max_iters,
            "head_sha": head_sha,
            "head_was_autofix": AUTOFIX_COMMIT_MARKER in (head_message or ""),
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

    def count_autofix_commits(self, *, repo: str, pr_number: int) -> int:
        owner, repo_name = repo.split("/", 1)
        gh = GitHubPRCommentClient(token=self._github_token)
        try:
            return gh.count_autofix_commits(
                owner, repo_name, pr_number, marker=AUTOFIX_COMMIT_MARKER
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
