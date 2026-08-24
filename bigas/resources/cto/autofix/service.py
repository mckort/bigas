"""Orchestrate Cursor autofix from a Bigas PR review comment."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from bigas.resources.cto.autofix.cursor_client import (
    CursorCloudAgentClient,
    CursorCloudAgentError,
)
from bigas.resources.cto.autofix.heuristics import (
    AUTOFIX_COMMIT_MARKER,
    autofix_cooldown_seconds,
    autofix_max_iterations,
    format_loop_protection_message,
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
9. If the review claims a helper/import is wrong (e.g. deleteField vs FieldValue.delete) but the repo already provides that helper via a local wrapper imported in the same file, treat the finding as already resolved — do not churn the code just to silence the review.
10. Before you finish: if your fixes left unused imports, functions, helpers, files, or replaced call sites, remove that dead code. Do not expand into a repo-wide cleanup.
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


def _age_seconds_since(iso_ts: Optional[str]) -> Optional[float]:
    if not iso_ts:
        return None
    raw = iso_ts.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())


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
        cooldown = autofix_cooldown_seconds()

        gh = GitHubPRCommentClient(token=self._github_token)

        try:
            pr = gh.get_pull_request(owner, repo_name, pr_number)
            if pr.get("merged") and not force:
                return {
                    "skipped": True,
                    "reason": "pr_already_merged",
                    "pr_url": pr_url,
                }
            head_sha, head_message, committed_at = gh.get_pr_head_commit_meta(
                owner, repo_name, pr_number
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
                "reason": format_loop_protection_message(
                    autofix_count=autofix_count, max_iterations=max_iters
                ),
                "pr_url": pr_url,
                "autofix_count": autofix_count,
                "max_iterations": max_iters,
                "head_sha": head_sha,
            }

        # Load the review comment early so we can decide whether cooldown applies.
        # Always fetch marked-comment metadata for review_updated_at (cooldown skip).
        # An explicit review_body override still uses that timestamp for cooldown
        # skip, but bypasses the stale_review gate (override = fresh caller input).
        body = (review_body or "").strip()
        explicit_review_body = bool(body)
        review_updated_at: Optional[str] = None
        try:
            marked = gh.get_marked_comment(
                owner=owner,
                repo=repo_name,
                pr_number=pr_number,
                marker=BIGAS_REVIEW_MARKER,
            )
        except GitHubPRCommentError as e:
            # If review_body was provided, we can continue without the metadata.
            if not body:
                raise AutofixError(str(e)) from e
            marked = None
            logger.warning(
                "Could not fetch Bigas review metadata for cooldown check: %s", e
            )

        if marked:
            # Always capture review timestamp for cooldown skip logic, even when
            # an explicit review_body override is provided.
            updated = marked.get("updated_at") or marked.get("created_at")
            review_updated_at = (
                updated.strip() if isinstance(updated, str) else None
            )
            if not explicit_review_body:
                found = marked.get("body") or ""
                body = found if isinstance(found, str) else ""

        if not body.strip():
            return {
                "skipped": True,
                "reason": "no Bigas review comment found on PR",
                "pr_url": pr_url,
                "autofix_count": autofix_count,
                "max_iterations": max_iters,
            }

        # Prevent overlapping launches while a previous autofix agent may still be
        # finishing. Skip cooldown when a newer Bigas review already exists after the
        # autofix head commit — that means the previous agent finished and we were
        # re-reviewed (common when the autofix push cancels/restarts Actions).
        #
        # The stale review check applies regardless of cooldown setting; only the
        # cooldown wait is gated by cooldown > 0.
        if not force and AUTOFIX_COMMIT_MARKER in (head_message or ""):
            age = _age_seconds_since(committed_at)
            review_age_after_head = None
            if review_updated_at and committed_at:
                head_age = _age_seconds_since(committed_at)
                review_age = _age_seconds_since(review_updated_at)
                if head_age is not None and review_age is not None:
                    # Positive => review comment is newer than the head commit.
                    review_age_after_head = head_age - review_age

            # Stale review check: applies regardless of cooldown setting.
            # Skip when an explicit review_body override is provided — that
            # override is treated as fresh input even if the PR comment is older.
            if (
                not explicit_review_body
                and review_age_after_head is not None
                and review_age_after_head <= 0
            ):
                # Review predates the autofix head commit — the review is stale.
                # Skip this run; a new Action run will trigger re-review on the
                # autofix commit, which will call autofix again with fresh findings.
                return {
                    "skipped": True,
                    "stale_review": True,
                    "reason": (
                        "Review predates the latest autofix commit; "
                        "waiting for re-review of autofix commit."
                    ),
                    "pr_url": pr_url,
                    "autofix_count": autofix_count,
                    "max_iterations": max_iters,
                    "head_sha": head_sha,
                }

            # Cooldown check: only applies when cooldown > 0.
            if cooldown > 0:
                if review_age_after_head is not None and review_age_after_head > 0:
                    logger.info(
                        "Skipping autofix cooldown for %s#%s: review is %.0fs newer than "
                        "autofix head %s",
                        f"{owner}/{repo_name}",
                        pr_number,
                        review_age_after_head,
                        head_sha[:8],
                    )
                elif age is not None and age < cooldown:
                    wait_left = int(cooldown - age)
                    return {
                        "skipped": True,
                        "cooldown": True,
                        "reason": (
                            f"autofix cooldown: latest commit is already `[bigas-autofix]` "
                            f"({int(age)}s ago). Wait ~{wait_left}s for the previous agent "
                            f"to finish before launching another round."
                        ),
                        "pr_url": pr_url,
                        "autofix_count": autofix_count,
                        "max_iterations": max_iters,
                        "cooldown_seconds": cooldown,
                        "head_age_seconds": int(age),
                        "head_sha": head_sha,
                    }

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
