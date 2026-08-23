"""
CTO resource endpoints: PR review and comment (and future CTO tools).
"""
from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, request

from bigas.resources.cto.chat_summaries import (
    SUMMARY_REPLY_INSTRUCTION,
    summarize_autofix_result,
    summarize_deploy_hotfix_result,
    summarize_followup_result,
    summarize_pr_merged_result,
    summarize_review_result,
    with_summary,
)
from bigas.resources.cto.autofix.heuristics import (
    auto_merge_enabled,
    autofix_max_iterations,
    autofix_pushed_new_commit,
    format_loop_protection_message,
    review_is_ready_to_merge,
)
from bigas.resources.cto.autofix.service import (
    AutofixError,
    AutofixService,
    autofix_looks_like_confirmation_stop,
)
from bigas.resources.cto.pr_review.github_client import (
    BIGAS_AUTOFIX_COOLDOWN_MARKER,
    BIGAS_REVIEW_MARKER,
    GitHubMergeNotReadyError,
    GitHubPRCommentClient,
    GitHubPRCommentError,
)
from bigas.resources.cto.pr_review.service import (
    PRReviewError,
    PRReviewResult,
    PRReviewService,
)
from bigas.discord_webhook import post_long_to_discord, post_to_discord
from bigas.github_refs import (
    format_pr_discord_line,
    is_owner_repo,
    parse_cursor_agent_id,
    resolve_repo_and_pr,
)
from bigas.resources.marketing.utils import sanitize_error_message
from bigas.providers.monitoring.service import MonitoringService, run_monitoring_checks
from bigas.resources.cto.qa_agent.service import QAAgentError, QAAgentService
from bigas.resources.cto.usage.service import (
    fetch_ai_usage,
    fetch_cursor_run_usage,
    format_weekly_cto_ai_report,
)

cto_bp = Blueprint(
    "cto_bp",
    __name__,
    url_prefix="/mcp/tools",
)

logger = logging.getLogger(__name__)

# Max characters for a GitHub comment to avoid API errors.


def _payload_text(data: dict) -> str:
    parts = []
    for key in ("pr_url", "repo", "url", "html_url", "agent_id", "agent_url"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    return " ".join(parts)


def _resolve_pr_from_payload(data: dict) -> tuple[str, int | None]:
    return resolve_repo_and_pr(
        repo=data.get("repo"),
        pr_number=data.get("pr_number"),
        text=_payload_text(data),
    )


def _json_summary(payload: dict, summarizer, status: int | None = None):
    body = jsonify(with_summary(payload, summarizer))
    if status is None:
        return body
    return body, status


def _resolve_agent_id_from_payload(data: dict, current: str = "") -> str:
    agent_id = (current or data.get("agent_id") or "").strip()
    parsed = parse_cursor_agent_id(" ".join(part for part in (agent_id, _payload_text(data)) if part))
    return parsed or agent_id


def _discord_llm_cost_line(review_result: PRReviewResult) -> str:
    """One-line list-price estimate for Discord done notifications."""
    usage = review_result.usage_dict()
    est = usage.get("est_cost_usd")
    if est is None:
        return ""
    attempts = usage.get("attempts") or review_result.attempts
    attempt_label = "attempt" if attempts == 1 else "attempts"
    return (
        f"Estimated LLM cost: ~${float(est):.4f} "
        f"({review_result.model}, {attempts} {attempt_label})"
    )


def _discord_review_posted_message(
    *,
    done_label: str,
    pr_url: str,
    comment_url: str,
    review_body: str,
    cost_suffix: str = "",
    pr_title: str = "",
) -> str:
    """Single Discord payload so the first chunk always identifies the PR."""
    comment_line = (
        f"Comment: {comment_url}"
        if (comment_url or "").strip()
        else "Comment: (no URL returned from GitHub.)"
    )
    return (
        f"{done_label}\n"
        f"{format_pr_discord_line(pr_url, pr_title)}\n"
        f"{comment_line}{cost_suffix}\n\n"
        f"---\n\n"
        f"{(review_body or '').strip()}"
    )


def _discord_cursor_usage_suffix(
    *,
    agent_id: str,
    run_id: str | None = None,
    cursor_api_key: str | None = None,
) -> str:
    """Best-effort Cursor usage lines for autofix Discord notifications."""
    try:
        result = fetch_cursor_run_usage(
            agent_id=agent_id,
            run_id=run_id,
            api_key=cursor_api_key,
        )
    except Exception:
        logger.warning("Cursor usage fetch failed for Discord", exc_info=True)
        return ""
    if not result.get("ok"):
        return ""
    lines = result.get("discord_lines") or []
    if not lines:
        return ""
    return "\n" + "\n".join(str(line) for line in lines)


def _resolve_pr_diff_text(
    *,
    diff: str | None,
    owner: str,
    repo_name: str,
    pr_number: int,
    github_token: str,
) -> str:
    """
    Prefer caller-supplied diff; if missing/blank, fetch the PR diff from GitHub.

    This avoids empty-diff races when Actions computes `git diff` after the PR
    was already merged into the base branch.
    """
    if isinstance(diff, str) and diff.strip():
        return diff
    return GitHubPRCommentClient(token=github_token).get_pr_diff(
        owner=owner,
        repo=repo_name,
        pr_number=pr_number,
    )

# The official limit is 65,536 bytes, so 60k chars is a safe buffer.
MAX_GITHUB_COMMENT_CHARS = 60_000


def _jira_final_approval_for_pr(
    *,
    repo: str,
    pr_number: int,
    pr_url: str,
    github_token: str,
    assume_merged: bool = False,
) -> dict:
    try:
        from bigas.resources.product.jira_automation.final_approval import (
            transition_issue_to_final_approval_for_pr,
        )

        return transition_issue_to_final_approval_for_pr(
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url,
            github_token=github_token,
            assume_merged=assume_merged,
        )
    except Exception:
        logger.warning("Jira final-approval hook failed", exc_info=True)
        return {"ok": False, "error": "final_approval_hook_exception"}


def _final_approval_after_merge(
    *,
    repo: str,
    pr_number: int,
    pr_url: str,
    github_token: str,
    merged: bool,
) -> dict:
    """Move the linked ticket only after the PR is actually merged."""
    if not merged:
        return {"skipped": True, "reason": "pr_not_merged"}
    return _jira_final_approval_for_pr(
        repo=repo,
        pr_number=pr_number,
        pr_url=pr_url,
        github_token=github_token,
        assume_merged=True,
    )


def _fetch_pull_request(
    *,
    owner: str,
    repo_name: str,
    pr_number: int,
    github_token: str,
) -> dict:
    """Best-effort PR JSON. Empty dict on missing token or fetch errors."""
    if not (github_token or "").strip():
        return {}
    try:
        pr = GitHubPRCommentClient(token=github_token).get_pull_request(
            owner=owner,
            repo=repo_name,
            pr_number=pr_number,
        )
    except GitHubPRCommentError:
        logger.warning(
            "Could not fetch PR %s/%s#%s",
            owner,
            repo_name,
            pr_number,
            exc_info=True,
        )
        return {}
    return pr if isinstance(pr, dict) else {}


def _pr_title_of(pr: dict | None) -> str:
    return ((pr or {}).get("title") or "").strip()


def _jira_issue_context_from_pr(pr: dict) -> tuple[str, str]:
    """Best-effort Jira issue key and summary from a PR dict (no status change)."""
    from bigas.resources.product.jira_automation.final_approval import (
        _resolve_issue_client,
        extract_jira_issue_key,
    )

    issue_key = (
        extract_jira_issue_key(
            (pr.get("title") or ""),
            (pr.get("body") or ""),
            ((pr.get("head") or {}).get("ref") or ""),
        )
        or ""
    )
    if not issue_key:
        return "", ""
    try:
        issue = _resolve_issue_client(issue_key).get_issue(
            issue_key, fields=["summary"]
        )
        summary = ((issue.get("fields") or {}).get("summary") or "").strip()
        return issue_key, summary
    except Exception:
        logger.debug(
            "Could not fetch Jira summary for %s", issue_key, exc_info=True
        )
        return issue_key, ""


def _jira_issue_heading(issue_key: str = "", issue_summary: str = "") -> str:
    from bigas.resources.product.jira_automation.comments import issue_discord_label

    key = (issue_key or "").strip()
    if not key:
        return ""
    return f" {issue_discord_label(key, issue_summary)}"


def _pr_already_merged(
    *,
    owner: str,
    repo_name: str,
    pr_number: int,
    github_token: str,
) -> bool:
    """Return True when the PR is already merged (best-effort; False on fetch errors)."""
    return bool(
        _fetch_pull_request(
            owner=owner,
            repo_name=repo_name,
            pr_number=pr_number,
            github_token=github_token,
        ).get("merged")
    )


def _maybe_auto_merge_pr(
    *,
    repo: str,
    pr_number: int,
    pr_url: str,
    github_token: str,
    issue_key: str = "",
    issue_summary: str = "",
) -> dict:
    """
    Squash-merge the PR when BIGAS_CTO_AUTO_MERGE is enabled.

    Tries an immediate merge first. If required checks (or similar) block it,
    falls back to GitHub native auto-merge so the PR merges once checks pass.
    Draft PRs are marked ready for review first — GitHub will not merge drafts.

    Best-effort: returns skipped/ok/error payload; posts Discord on success or failure.
    """
    issue_bit = _jira_issue_heading(issue_key, issue_summary)
    if not auto_merge_enabled():
        return {"skipped": True, "reason": "BIGAS_CTO_AUTO_MERGE not enabled"}
    if not (github_token or "").strip():
        err = "GITHUB_TOKEN missing"
        _post_cto_status(
            f"**PR auto-merge failed**{issue_bit}\n"
            f"{format_pr_discord_line(pr_url)}\nReason: {err}"
        )
        return {"ok": False, "merged": False, "error": err}
    if "/" not in repo or repo.count("/") != 1:
        err = "repo must be owner/repo"
        _post_cto_status(
            f"**PR auto-merge failed**{issue_bit}\n"
            f"{format_pr_discord_line(pr_url)}\nReason: {err}"
        )
        return {"ok": False, "merged": False, "error": err}

    owner, repo_name = repo.split("/", 1)
    client = GitHubPRCommentClient(token=github_token)

    pr: dict = {}
    try:
        pr = client.get_pull_request(
            owner=owner, repo=repo_name, pr_number=pr_number
        )
    except GitHubPRCommentError:
        logger.warning(
            "Could not fetch PR before auto-merge for %s/%s#%s",
            owner,
            repo_name,
            pr_number,
            exc_info=True,
        )
    pr_title = _pr_title_of(pr)
    pr_ref = format_pr_discord_line(pr_url, pr_title)
    if not (issue_key or "").strip():
        from bigas.resources.product.jira_automation.final_approval import (
            extract_jira_issue_key,
        )

        issue_key = (
            extract_jira_issue_key(
                pr_title,
                (pr.get("body") or ""),
                ((pr.get("head") or {}).get("ref") or ""),
            )
            or ""
        )
        issue_bit = _jira_issue_heading(issue_key, issue_summary)

    # Quiet skip when a parallel review already merged (no Discord spam).
    if pr.get("merged"):
        return {
            "skipped": True,
            "reason": "pr_already_merged",
            "merged": True,
            "ok": True,
        }

    converted_draft = False
    if pr.get("draft"):
        try:
            client.mark_pull_request_ready_for_review(
                owner=owner,
                repo=repo_name,
                pr_number=pr_number,
                node_id=pr.get("node_id"),
            )
            converted_draft = True
            logger.info(
                "Marked %s#%s ready for review before auto-merge (was draft)",
                repo,
                pr_number,
            )
        except GitHubPRCommentError as e:
            err = sanitize_error_message(str(e))
            logger.warning(
                "Could not mark %s#%s ready for review: %s",
                repo,
                pr_number,
                err,
            )
            _post_cto_status(
                f"**PR auto-merge failed**{issue_bit}\n{pr_ref}\n"
                f"PR is still a draft; could not mark ready for review.\n"
                f"Reason: {err}"
            )
            return {
                "ok": False,
                "merged": False,
                "draft": True,
                "draft_converted": False,
                "error": err,
            }

    draft_line = (
        "\nMarked draft as ready for review." if converted_draft else ""
    )

    try:
        result = client.merge_pull_request(
            owner=owner,
            repo=repo_name,
            pr_number=pr_number,
            merge_method="squash",
        )
    except GitHubMergeNotReadyError as e:
        sync_err = sanitize_error_message(str(e))
        # Already merged races often surface as 405 "not mergeable".
        if _pr_already_merged(
            owner=owner,
            repo_name=repo_name,
            pr_number=pr_number,
            github_token=github_token,
        ):
            return {
                "skipped": True,
                "reason": "pr_already_merged",
                "merged": True,
                "ok": True,
                "sync_error": sync_err,
            }
        logger.info(
            "Immediate merge not ready for %s#%s (%s); enabling GitHub auto-merge",
            repo,
            pr_number,
            sync_err,
        )
        try:
            enabled = client.enable_pull_request_auto_merge(
                owner=owner,
                repo=repo_name,
                pr_number=pr_number,
                merge_method="squash",
            )
        except GitHubPRCommentError as enable_err:
            err = sanitize_error_message(str(enable_err))
            logger.warning(
                "Auto-merge enable failed for %s#%s after sync block: %s",
                repo,
                pr_number,
                err,
            )
            _post_cto_status(
                f"**PR auto-merge failed**{issue_bit}\n{pr_ref}\n"
                f"Immediate merge blocked: {sync_err}\n"
                f"Enable auto-merge failed: {err}"
            )
            return {
                "ok": False,
                "merged": False,
                "auto_merge_enabled": False,
                "draft_converted": converted_draft,
                "error": err,
                "sync_error": sync_err,
            }
        except Exception as enable_err:
            err = sanitize_error_message(str(enable_err))
            logger.warning(
                "Auto-merge enable unexpected error for %s#%s",
                repo,
                pr_number,
                exc_info=True,
            )
            _post_cto_status(
                f"**PR auto-merge failed**{issue_bit}\n{pr_ref}\nReason: {err}"
            )
            return {
                "ok": False,
                "merged": False,
                "auto_merge_enabled": False,
                "draft_converted": converted_draft,
                "error": err,
                "sync_error": sync_err,
            }

        method = (enabled.get("merge_method") or "squash").lower()
        _post_cto_status(
            f"**PR auto-merge enabled** ({method}){issue_bit}{draft_line}\n"
            f"Waiting for required checks, then GitHub will squash-merge.\n"
            f"{pr_ref}"
        )
        return {
            "ok": True,
            "merged": False,
            "auto_merge_enabled": True,
            "draft_converted": converted_draft,
            "merge_method": method,
            "enabled_at": enabled.get("enabled_at"),
            "sync_error": sync_err,
        }
    except GitHubPRCommentError as e:
        err = sanitize_error_message(str(e))
        logger.warning("Auto-merge failed for %s#%s: %s", repo, pr_number, err)
        _post_cto_status(
            f"**PR auto-merge failed**{issue_bit}\n{pr_ref}\nReason: {err}"
        )
        return {
            "ok": False,
            "merged": False,
            "draft_converted": converted_draft,
            "error": err,
        }
    except Exception as e:
        err = sanitize_error_message(str(e))
        logger.warning("Auto-merge unexpected error for %s#%s", repo, pr_number, exc_info=True)
        _post_cto_status(
            f"**PR auto-merge failed**{issue_bit}\n{pr_ref}\nReason: {err}"
        )
        return {
            "ok": False,
            "merged": False,
            "draft_converted": converted_draft,
            "error": err,
        }

    sha = (result.get("sha") or "").strip()
    sha_line = f"\nSHA: `{sha}`" if sha else ""
    _post_cto_status(
        f"**PR auto-merged** (squash){issue_bit}{draft_line}\n{pr_ref}{sha_line}"
    )
    return {
        "ok": True,
        "merged": True,
        "auto_merge_enabled": False,
        "draft_converted": converted_draft,
        "merge_method": "squash",
        "sha": sha or None,
        "message": (result.get("message") or "").strip() or None,
    }


def _notify_autofix_loop_protection(
    *,
    repo: str,
    pr_number: int,
    pr_url: str,
    autofix_count: int,
    max_iterations: int,
    github_token: str = "",
) -> None:
    detail = format_loop_protection_message(
        autofix_count=autofix_count, max_iterations=max_iterations
    )
    token = (github_token or os.environ.get("GITHUB_TOKEN") or "").strip()
    pr: dict = {}
    if token and "/" in repo and repo.count("/") == 1:
        owner, name = repo.split("/", 1)
        pr = _fetch_pull_request(
            owner=owner,
            repo_name=name,
            pr_number=pr_number,
            github_token=token,
        )
    _post_to_discord_cto(
        f"**CTO autofix stopped (loop protection)**\n"
        f"{detail}\n"
        f"{format_pr_discord_line(pr_url, _pr_title_of(pr))}"
    )
    # Best-effort Jira comment on linked issue (no status change).
    try:
        from bigas.resources.product.create_release_notes.jira_client import (
            JiraClient,
            JiraConfig,
        )
        from bigas.resources.product.jira_automation.config import BIGAS_COMMENT_MARKER
        from bigas.resources.product.jira_automation.final_approval import (
            extract_jira_issue_key,
        )

        if not pr:
            return
        issue_key = extract_jira_issue_key(
            (pr.get("title") or ""),
            (pr.get("body") or ""),
            ((pr.get("head") or {}).get("ref") or ""),
        )
        if not issue_key:
            return
        jira = JiraClient(JiraConfig.from_env())
        jira.add_comment(
            issue_key,
            f"{BIGAS_COMMENT_MARKER} Autofix loop protection.\n"
            f"{detail}\n"
            f"Left in current status.\nPR: {pr_url}",
        )
    except Exception:
        logger.warning("Loop-protection Jira comment failed", exc_info=True)


def _cto_discord_webhook() -> str:
    webhook = (os.environ.get("DISCORD_WEBHOOK_URL_CTO") or "").strip()
    if not webhook or webhook.startswith("placeholder"):
        return ""
    return webhook


def _post_to_discord_cto(message: str, *, mirror_thread: bool = True) -> None:
    """Post to CTO Discord and the CTO chat thread.
    Callers must pass only sanitized messages (use sanitize_error_message for errors) to avoid leaking tokens.
    Set mirror_thread=False for review results and pipeline status cards (Activity + Discord only).
    """
    webhook = _cto_discord_webhook()
    if not webhook:
        logger.info("DISCORD_WEBHOOK_URL_CTO not set or placeholder, skipping Discord post")
    post_to_discord(
        webhook, message, chat_agent_id="cto", mirror_thread=mirror_thread
    )


def _post_cto_status(message: str) -> None:
    """PR review/pipeline cards: Discord + Activity, not the CTO chat thread."""
    _post_to_discord_cto(message, mirror_thread=False)


def _post_to_discord_cto_chunks(message: str) -> None:
    """Post long CTO review content: Discord + Activity, not the CTO thread."""
    if not (message or "").strip():
        return
    post_long_to_discord(
        _cto_discord_webhook(),
        message.strip(),
        chat_agent_id="cto",
        mirror_thread=False,
    )


@cto_bp.route("/review_and_comment_pr", methods=["POST"])
def review_and_comment_pr():
    """
    Review a pull request diff with AI (Codex) and post or update a single PR comment.

    Request JSON:
      - repo (str, required unless pr_url): "owner/repo"
      - pr_number (int, required unless pr_url): pull request number
      - pr_url (str, optional): GitHub pull request URL; alternative to repo + pr_number
      - diff (str, optional): PR diff text; if omitted or empty, Bigas fetches it from GitHub
      - instructions (str, optional): extra instructions for the reviewer
      - github_token (str, optional): override GitHub PAT (else uses GITHUB_TOKEN env)
      - llm_model (str, optional): override model for this request (default: gemini-3.1-pro-preview)

    Returns:
      - success, comment_url, review_posted; or error with status 4xx/5xx.
    """
    data = request.get_json(silent=True) or {}
    phase = (data.get("phase") or "initial").strip().lower()
    if phase not in {"initial", "post_autofix"}:
        phase = "initial"

    repo, pr_number = _resolve_pr_from_payload(data)
    diff = data.get("diff")
    instructions = (data.get("instructions") or "").strip() or None
    github_token = (data.get("github_token") or "").strip() or os.environ.get("GITHUB_TOKEN") or ""
    llm_model = (data.get("llm_model") or "").strip() or None

    def _fail(reason: str, status: int, error: str | None = None):
        # Input / pre-start failures stay in the HTTP response only.
        # Discord is reserved for outcomes after "CTO PR review started".
        return _json_summary({"error": error or reason}, summarize_review_result, status)

    if not is_owner_repo(repo):
        return _fail("repo is required.", 400, "repo is required (e.g. 'owner/repo')")
    if pr_number is None:
        return _fail("pr_number is required.", 400)
    if diff is not None and not isinstance(diff, str):
        return _fail("diff must be a string.", 400)
    if not github_token:
        return _fail(
            "GitHub token is required (GITHUB_TOKEN or github_token).",
            400,
            "GitHub token is required. Set GITHUB_TOKEN in env or pass github_token in the request.",
        )

    owner, repo_name = repo.split("/", 1)
    pr_url = f"https://github.com/{repo}/pull/{pr_number}"
    pr = _fetch_pull_request(
        owner=owner,
        repo_name=repo_name,
        pr_number=pr_number,
        github_token=github_token,
    )
    pr_title = _pr_title_of(pr)
    pr_ref = format_pr_discord_line(pr_url, pr_title)

    # Parallel Actions runs (or a late followup) should not re-review / Discord
    # after the PR was already squash-merged. Still move the linked ticket.
    if pr.get("merged"):
        jira_final = _final_approval_after_merge(
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url,
            github_token=github_token,
            merged=True,
        )
        return _json_summary(
            {
                "success": True,
                "skipped": True,
                "reason": "pr_already_merged",
                "ready_to_merge": True,
                "review_posted": False,
                "phase": phase,
                "pr_url": pr_url,
                "jira_final_approval": jira_final,
            },
            summarize_review_result,
        )

    try:
        diff = _resolve_pr_diff_text(
            diff=diff,
            owner=owner,
            repo_name=repo_name,
            pr_number=pr_number,
            github_token=github_token,
        )
    except GitHubPRCommentError as e:
        err_msg = sanitize_error_message(str(e))
        status = 502
        if "401" in err_msg or "invalid" in err_msg.lower() or "expired" in err_msg.lower():
            status = 401
        elif "403" in err_msg:
            status = 403
        elif "404" in err_msg or "not found" in err_msg.lower():
            status = 404
        return _fail(err_msg, status)

    if not (diff or "").strip():
        return _fail("diff is empty (PR has no file changes, or GitHub returned an empty diff).", 400)

    # Notify Discord only once we have a usable diff (avoids started+failed spam).
    if phase == "post_autofix":
        _post_to_discord_cto(
            f"**CTO PR re-review after autofix started**\n{pr_ref}"
        )
    else:
        _post_to_discord_cto(
            f"**CTO PR review started**\n{pr_ref}"
        )

    previous_review = None
    if phase == "post_autofix":
        try:
            previous_review = GitHubPRCommentClient(token=github_token).get_marked_comment_body(
                owner=owner,
                repo=repo_name,
                pr_number=pr_number,
                marker=BIGAS_REVIEW_MARKER,
            )
        except GitHubPRCommentError:
            logger.warning("Could not load previous Bigas review for post_autofix", exc_info=True)

    try:
        review_service = PRReviewService(openai_model=llm_model)
        review_result = review_service.review(
            diff=diff,
            instructions=instructions,
            phase=phase,  # type: ignore[arg-type]
            previous_review=previous_review,
        )
        review_body = review_result.text
    except PRReviewError as e:
        logger.warning("PR review failed: %s", e)
        _post_cto_status(f"**CTO PR review done**\nNo comment posted.\nReason: {sanitize_error_message(str(e))}")
        return _json_summary({"error": sanitize_error_message(str(e))}, summarize_review_result, 500)

    if len(review_body) > MAX_GITHUB_COMMENT_CHARS:
        logger.warning(
            "Review body truncated to %d characters to fit GitHub comment limit.",
            MAX_GITHUB_COMMENT_CHARS,
        )
        review_body = (
            review_body[:MAX_GITHUB_COMMENT_CHARS]
            + "\n\n---\n\n_Review truncated for GitHub comment length._"
        )

    comment_url = ""
    try:
        client = GitHubPRCommentClient(token=github_token)
        result = client.post_or_update_pr_comment(
            owner=owner,
            repo=repo_name,
            pr_number=pr_number,
            body=review_body,
            marker=BIGAS_REVIEW_MARKER,
        )
        comment_url = result.get("html_url") or ""
    except GitHubPRCommentError as e:
        logger.warning("GitHub PR comment failed: %s", e)
        err_msg = sanitize_error_message(str(e))
        _post_cto_status(f"**CTO PR review done**\nNo comment posted.\nReason: {err_msg}")
        if "401" in str(e) or "invalid" in str(e).lower() or "expired" in str(e).lower():
            return _json_summary({"error": err_msg}, summarize_review_result, 401)
        if "403" in str(e):
            return _json_summary({"error": err_msg}, summarize_review_result, 403)
        if "404" in str(e) or "not found" in str(e).lower():
            return _json_summary({"error": err_msg}, summarize_review_result, 404)
        return _json_summary({"error": err_msg}, summarize_review_result, 502)

    ready = review_is_ready_to_merge(review_body)
    done_label = (
        "**CTO PR re-review after autofix done**"
        if phase == "post_autofix"
        else "**CTO PR review done**"
    )
    cost_line = _discord_llm_cost_line(review_result)
    cost_suffix = f"\n{cost_line}" if cost_line else ""
    if comment_url:
        _post_to_discord_cto_chunks(
            _discord_review_posted_message(
                done_label=done_label,
                pr_url=pr_url,
                comment_url=comment_url,
                review_body=review_body,
                cost_suffix=cost_suffix,
                pr_title=pr_title,
            )
        )
    else:
        _post_cto_status(
            f"{done_label}\nNo comment posted. (No URL returned from GitHub.)"
            f"{cost_suffix}\n{pr_ref}"
        )

    auto_merge: dict = {"skipped": True, "reason": "not_ready"}
    if ready:
        _post_cto_status(
            f"**Ready to merge**\n{pr_ref}\n"
            + (f"Comment: {comment_url}" if comment_url else "")
        )
        issue_key, issue_summary = _jira_issue_context_from_pr(pr)
        auto_merge = _maybe_auto_merge_pr(
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url,
            github_token=github_token,
            issue_key=issue_key,
            issue_summary=issue_summary,
        )
        jira_final = _final_approval_after_merge(
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url,
            github_token=github_token,
            merged=bool(auto_merge.get("merged")),
        )
    else:
        jira_final = {"skipped": True, "reason": "not_ready"}

    return _json_summary({
        "success": True,
        "comment_url": comment_url,
        "review_posted": bool(comment_url),
        "used_model": review_result.model,
        "usage": review_result.usage_dict(),
        "ready_to_merge": ready,
        "phase": phase,
        "jira_final_approval": jira_final,
        "auto_merge": auto_merge,
        "pr_url": pr_url,
    }, summarize_review_result)


@cto_bp.route("/notify_pr_merged", methods=["POST"])
def notify_pr_merged():
    """
    Move the linked board ticket to Final approval after a PR is merged.

    Used by the PR-closed GitHub Action so a human merge (or delayed GitHub
    auto-merge) still advances the card. Idempotent if the card is already there.

    Request JSON:
      - repo (str, required unless pr_url): "owner/repo"
      - pr_number (int, required unless pr_url)
      - pr_url (str, optional)
      - github_token (str, optional)
    """
    data = request.get_json(silent=True) or {}
    repo, pr_number = _resolve_pr_from_payload(data)
    github_token = (
        (data.get("github_token") or "").strip()
        or os.environ.get("GITHUB_TOKEN")
        or ""
    )

    if not is_owner_repo(repo):
        return _json_summary(
            {"error": "repo is required (e.g. 'owner/repo')"},
            summarize_pr_merged_result,
            400,
        )
    if pr_number is None:
        return _json_summary(
            {"error": "pr_number is required"},
            summarize_pr_merged_result,
            400,
        )
    if not github_token:
        return _json_summary(
            {"error": "GitHub token is required. Set GITHUB_TOKEN in env or pass github_token."},
            summarize_pr_merged_result,
            400,
        )

    pr_url = f"https://github.com/{repo}/pull/{pr_number}"
    result = _jira_final_approval_for_pr(
        repo=repo,
        pr_number=pr_number,
        pr_url=pr_url,
        github_token=github_token,
        assume_merged=False,
    )
    payload = {
        "success": bool(result.get("ok") or result.get("skipped")),
        "pr_url": pr_url,
        "jira_final_approval": result,
    }
    if result.get("error") and not result.get("ok"):
        payload["success"] = False
        payload["error"] = result.get("error")
        return _json_summary(payload, summarize_pr_merged_result, 502)
    return _json_summary(payload, summarize_pr_merged_result)


@cto_bp.route("/autofix_pr", methods=["POST"])
def autofix_pr():
    """
    Launch a Cursor cloud agent to fix actionable findings from the Bigas PR review.

    Request JSON:
      - repo (str, required unless pr_url): "owner/repo"
      - pr_number (int, required unless pr_url)
      - pr_url (str, optional): GitHub pull request URL; alternative to repo + pr_number
      - force (bool, optional): bypass clean-review / autofix-loop guards
      - review_body (str, optional): override; else fetch Bigas-marked PR comment
      - github_token (str, optional): override GITHUB_TOKEN
      - cursor_api_key (str, optional): override CURSOR_API_KEY
      - is_retry (bool, optional): suppress Discord skip notifications on Actions
          cooldown/retry polls (cooldown itself never posts Discord)
    """
    data = request.get_json(silent=True) or {}
    repo, pr_number = _resolve_pr_from_payload(data)
    force = bool(data.get("force") or False)
    is_retry = bool(data.get("is_retry") or False)
    review_body = data.get("review_body")
    if isinstance(review_body, str):
        review_body = review_body.strip() or None
    else:
        review_body = None
    github_token = (data.get("github_token") or "").strip() or None
    cursor_api_key = (data.get("cursor_api_key") or "").strip() or None

    if not is_owner_repo(repo):
        return _json_summary(
            {"error": "repo is required in the form 'owner/repo'"},
            summarize_autofix_result,
            400,
        )
    if pr_number is None:
        return _json_summary(
            {"error": "pr_number is required"},
            summarize_autofix_result,
            400,
        )

    pr_url = f"https://github.com/{repo}/pull/{pr_number}"
    owner, repo_name = repo.split("/", 1)
    gh_token = (github_token or os.environ.get("GITHUB_TOKEN") or "").strip()
    pr_title = _pr_title_of(
        _fetch_pull_request(
            owner=owner,
            repo_name=repo_name,
            pr_number=pr_number,
            github_token=gh_token,
        )
    )
    pr_ref = format_pr_discord_line(pr_url, pr_title)

    try:
        service = AutofixService(
            cursor_api_key=cursor_api_key,
            github_token=github_token,
        )
        result = service.run(
            repo=repo,
            pr_number=pr_number,
            force=force,
            review_body=review_body,
        )
    except AutofixError as e:
        err = sanitize_error_message(str(e))
        logger.warning("Autofix failed: %s", e)
        _post_to_discord_cto(
            f"**CTO autofix done**\nNot launched.\nReason: {err}\n{pr_ref}"
        )
        if "401" in str(e) or "auth" in str(e).lower():
            status = 401
        elif "403" in str(e):
            status = 403
        elif "404" in str(e):
            status = 404
        elif "CURSOR_API_KEY" in str(e) or "GITHUB_TOKEN" in str(e):
            status = 400
        else:
            status = 502
        return _json_summary({"error": err, "pr_url": pr_url}, summarize_autofix_result, status)

    if result.get("skipped"):
        reason = result.get("reason") or "skipped"
        if result.get("loop_protection"):
            _notify_autofix_loop_protection(
                repo=repo,
                pr_number=pr_number,
                pr_url=pr_url,
                autofix_count=int(result.get("autofix_count") or 0),
                max_iterations=int(
                    result.get("max_iterations") or autofix_max_iterations()
                ),
                github_token=github_token or "",
            )
        elif result.get("review_clean"):
            _post_to_discord_cto(
                f"**CTO autofix skipped**\n"
                f"Review does not need autofix ({sanitize_error_message(reason)}).\n"
                f"{pr_ref}"
            )
        elif result.get("cooldown"):
            reason = result.get("reason") or "cooldown"
            wait_left = None
            try:
                cooldown_s = int(result.get("cooldown_seconds") or 0)
                age_s = int(result.get("head_age_seconds") or 0)
                if cooldown_s > 0:
                    wait_left = max(0, cooldown_s - age_s)
            except (TypeError, ValueError):
                wait_left = None
            mins = max(1, int((wait_left + 59) // 60)) if wait_left is not None else None
            wait_txt = f"~{mins} min" if mins is not None else "a few minutes"
            # No Discord for cooldowns — Action polling would spam. PR comment is enough.
            # Visible on the PR so cooldown does not look like a silent hang.
            gh_token = github_token or (os.environ.get("GITHUB_TOKEN") or "").strip()
            if gh_token:
                try:
                    owner, repo_name = repo.split("/", 1)
                    body = (
                        "### Autofix paused (cooldown)\n\n"
                        f"Latest head commit is already `[bigas-autofix]`. "
                        f"Waiting **{wait_txt}** before launching another Cursor agent "
                        "so agents do not overlap.\n\n"
                        "The Actions autofix loop will retry automatically after the wait. "
                        "You can also re-run **Bigas PR review** manually."
                    )
                    GitHubPRCommentClient(token=gh_token).post_or_update_pr_comment(
                        owner=owner,
                        repo=repo_name,
                        pr_number=int(pr_number),
                        body=body,
                        marker=BIGAS_AUTOFIX_COOLDOWN_MARKER,
                    )
                except Exception:
                    logger.warning(
                        "Failed to post autofix cooldown PR comment",
                        exc_info=True,
                    )
        elif result.get("stale_review"):
            logger.info(
                "Autofix skipped for %s#%s: stale review predates latest autofix commit",
                repo,
                pr_number,
            )
            if not is_retry:
                _post_to_discord_cto(
                    f"**CTO autofix skipped (stale review)**\n"
                    f"Review predates the latest autofix commit; waiting for re-review.\n"
                    f"{pr_ref}"
                )
        elif not is_retry:
            _post_to_discord_cto(
                f"**CTO autofix skipped**\n"
                f"Reason: {sanitize_error_message(reason)}\n{pr_ref}"
            )
        # Delete any stale cooldown comment when skipped for non-cooldown reasons.
        if not result.get("cooldown"):
            gh_token = github_token or (os.environ.get("GITHUB_TOKEN") or "").strip()
            if gh_token:
                try:
                    owner, repo_name = repo.split("/", 1)
                    GitHubPRCommentClient(token=gh_token).delete_marked_comment(
                        owner=owner,
                        repo=repo_name,
                        pr_number=int(pr_number),
                        marker=BIGAS_AUTOFIX_COOLDOWN_MARKER,
                    )
                except Exception:
                    logger.warning(
                        "Failed to delete autofix cooldown PR comment on skip",
                        exc_info=True,
                    )
        return _json_summary({"success": True, **result}, summarize_autofix_result)

    agent_url = result.get("agent_url") or ""
    agent_id = result.get("agent_id") or ""
    round_n = result.get("autofix_round") or "?"
    max_n = result.get("max_iterations") or autofix_max_iterations()

    # Delete any stale cooldown comment now that autofix is launching.
    gh_token = github_token or (os.environ.get("GITHUB_TOKEN") or "").strip()
    if gh_token:
        try:
            owner, repo_name = repo.split("/", 1)
            GitHubPRCommentClient(token=gh_token).delete_marked_comment(
                owner=owner,
                repo=repo_name,
                pr_number=int(pr_number),
                marker=BIGAS_AUTOFIX_COOLDOWN_MARKER,
            )
        except Exception:
            logger.warning(
                "Failed to delete autofix cooldown PR comment",
                exc_info=True,
            )

    _post_to_discord_cto(
        f"**CTO autofix launched** ({round_n}/{max_n})\n"
        f"{pr_ref}\nAgent: {agent_url or agent_id}"
    )
    return _json_summary({"success": True, **result}, summarize_autofix_result)


@cto_bp.route("/autofix_followup", methods=["POST"])
def autofix_followup():
    """
    Poll Cursor autofix status; optionally finalize (Discord + re-review) once done.

    Request JSON:
      - repo, pr_number (required unless pr_url), agent_id (required unless a cursor.com/agents URL is given)
      - pr_url (optional): GitHub pull request URL; alternative to repo + pr_number
      - run_id (optional)
      - baseline_head_sha (optional): PR head SHA at autofix launch; required to detect
        whether *this* agent pushed a new `[bigas-autofix]` commit
      - autofix_round (optional): launched round number for Discord copy (e.g. 2 of 5)
      - finalize (bool, default false): if true and agent terminal, Discord + re-review
    """
    data = request.get_json(silent=True) or {}
    repo, pr_number = _resolve_pr_from_payload(data)
    agent_id = _resolve_agent_id_from_payload(data)
    run_id = (data.get("run_id") or "").strip() or None
    baseline_head_sha = (data.get("baseline_head_sha") or data.get("head_sha") or "").strip() or None
    autofix_round = data.get("autofix_round")
    try:
        autofix_round_n = int(autofix_round) if autofix_round is not None else None
    except (TypeError, ValueError):
        autofix_round_n = None
    finalize = bool(data.get("finalize") or False)
    github_token = (data.get("github_token") or "").strip() or None
    cursor_api_key = (data.get("cursor_api_key") or "").strip() or None

    if not is_owner_repo(repo):
        return _json_summary(
            {"error": "repo is required in the form 'owner/repo'"},
            summarize_followup_result,
            400,
        )
    if pr_number is None:
        return _json_summary(
            {"error": "pr_number is required"},
            summarize_followup_result,
            400,
        )
    if not agent_id:
        return _json_summary(
            {"error": "agent_id is required"},
            summarize_followup_result,
            400,
        )

    pr_url = f"https://github.com/{repo}/pull/{pr_number}"
    owner, repo_name = repo.split("/", 1)

    try:
        service = AutofixService(
            cursor_api_key=cursor_api_key,
            github_token=github_token,
        )
        status = service.poll_status(agent_id=agent_id, run_id=run_id)
    except AutofixError as e:
        err = sanitize_error_message(str(e))
        return _json_summary({"error": err}, summarize_followup_result, 502)

    base = {
        "success": True,
        "done": status.get("done", False),
        "ok": status.get("ok", False),
        "asked_confirmation": status.get("asked_confirmation", False),
        "status": status.get("status", "UNKNOWN"),
        "agent_id": status.get("agent_id"),
        "run_id": status.get("run_id"),
        "agent_url": status.get("agent_url"),
        "pr_url": pr_url,
        "finalized": False,
    }

    if not status.get("done") or not finalize:
        return _json_summary(base, summarize_followup_result)

    gh_token = (github_token or os.environ.get("GITHUB_TOKEN") or "").strip()
    pr = _fetch_pull_request(
        owner=owner,
        repo_name=repo_name,
        pr_number=pr_number,
        github_token=gh_token,
    ) if gh_token else {}
    pr_title = _pr_title_of(pr)
    pr_ref = format_pr_discord_line(pr_url, pr_title)
    if pr.get("merged"):
        # Another path already merged (or a late finalize after auto-merge).
        jira_final = _final_approval_after_merge(
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url,
            github_token=gh_token,
            merged=True,
        )
        base.update(
            {
                "finalized": True,
                "skipped": True,
                "reason": "pr_already_merged",
                "ready_to_merge": True,
                "fixes_pushed": False,
                "rereviewed": False,
                "jira_final_approval": jira_final,
            }
        )
        return _json_summary(base, summarize_followup_result)

    agent_url = status.get("agent_url") or ""
    run_status = status.get("status") or "UNKNOWN"
    usage_suffix = _discord_cursor_usage_suffix(
        agent_id=agent_id,
        run_id=status.get("run_id") or run_id,
        cursor_api_key=cursor_api_key,
    )
    if not status.get("ok"):
        _post_to_discord_cto(
            f"**CTO autofix failed**\n{pr_ref}\n"
            f"Status: {run_status}\nAgent: {agent_url}{usage_suffix}"
        )
        base.update({"finalized": True, "ready_to_merge": False, "fixes_pushed": False})
        return _json_summary(base, summarize_followup_result)

    fixes_pushed = False
    head_sha = ""
    head_message = ""
    try:
        head_sha, head_message = service.get_pr_head_commit(
            repo=repo, pr_number=pr_number
        )
        fixes_pushed = autofix_pushed_new_commit(
            head_sha=head_sha,
            head_message=head_message,
            baseline_head_sha=baseline_head_sha,
        )
    except AutofixError as e:
        logger.warning("Could not read PR head after autofix: %s", e)

    result_text = status.get("result_text") or ""
    asked_confirm = autofix_looks_like_confirmation_stop(result_text)
    if not fixes_pushed:
        round_bit = ""
        if autofix_round_n is not None:
            round_bit = f" (launched as round {autofix_round_n})"
        if asked_confirm:
            why = "Agent appears to have stopped to ask for confirmation."
        elif baseline_head_sha and head_sha and baseline_head_sha == head_sha:
            why = (
                "PR head SHA unchanged since launch — agent finished without pushing "
                "a new `[bigas-autofix]` commit (same review would be re-posted otherwise)."
            )
        else:
            why = (
                "No new `[bigas-autofix]` commit on PR head "
                "(nothing pushed, or agent only proposed changes)."
            )
        _post_to_discord_cto(
            f"**CTO autofix finished without commits**{round_bit}\n{pr_ref}\n"
            f"{why}\nAgent: {agent_url}{usage_suffix}\n"
            f"Stopping autofix loop for this run (no re-review without a new commit)."
        )
        base.update(
            {
                "finalized": True,
                "ready_to_merge": False,
                "fixes_pushed": False,
                "asked_confirmation": asked_confirm,
                "head_sha": head_sha,
                "baseline_head_sha": baseline_head_sha,
                "autofix_round": autofix_round_n,
                "rereviewed": False,
            }
        )
        return _json_summary(base, summarize_followup_result)

    _post_to_discord_cto(
        f"**CTO autofix completed**\n{pr_ref}\n"
        f"Fixes pushed to the PR branch.\nAgent: {agent_url}{usage_suffix}"
    )

    try:
        diff = service.fetch_pr_diff(repo=repo, pr_number=pr_number)
    except AutofixError as e:
        err = sanitize_error_message(str(e))
        _post_cto_status(
            f"**CTO PR re-review after autofix done**\nNo comment posted.\nReason: {err}"
        )
        return _json_summary(
            {"error": err, **base, "finalized": True, "rereviewed": False},
            summarize_followup_result,
            502,
        )

    gh_token = github_token or os.environ.get("GITHUB_TOKEN") or ""
    _post_to_discord_cto(f"**CTO PR re-review after autofix started**\n{pr_ref}")
    previous_review = None
    try:
        previous_review = GitHubPRCommentClient(token=gh_token).get_marked_comment_body(
            owner=owner,
            repo=repo_name,
            pr_number=pr_number,
            marker=BIGAS_REVIEW_MARKER,
        )
    except GitHubPRCommentError:
        logger.warning("Could not load previous Bigas review before post_autofix", exc_info=True)
    try:
        review_service = PRReviewService()
        review_result = review_service.review(
            diff=diff,
            phase="post_autofix",
            previous_review=previous_review,
        )
        review_body = review_result.text
    except PRReviewError as e:
        err = sanitize_error_message(str(e))
        _post_cto_status(
            f"**CTO PR re-review after autofix done**\nNo comment posted.\nReason: {err}"
        )
        return _json_summary(
            {"error": err, **base, "finalized": True, "rereviewed": False},
            summarize_followup_result,
            500,
        )

    if len(review_body) > MAX_GITHUB_COMMENT_CHARS:
        review_body = (
            review_body[:MAX_GITHUB_COMMENT_CHARS]
            + "\n\n---\n\n_Review truncated for GitHub comment length._"
        )

    comment_url = ""
    try:
        client = GitHubPRCommentClient(token=gh_token)
        posted = client.post_or_update_pr_comment(
            owner=owner,
            repo=repo_name,
            pr_number=pr_number,
            body=review_body,
            marker=BIGAS_REVIEW_MARKER,
        )
        comment_url = posted.get("html_url") or ""
    except GitHubPRCommentError as e:
        err = sanitize_error_message(str(e))
        _post_cto_status(
            f"**CTO PR re-review after autofix done**\nNo comment posted.\nReason: {err}"
        )
        return _json_summary(
            {"error": err, **base, "finalized": True, "rereviewed": False},
            summarize_followup_result,
            502,
        )

    ready = review_is_ready_to_merge(review_body)
    cost_line = _discord_llm_cost_line(review_result)
    cost_suffix = f"\n{cost_line}" if cost_line else ""
    _post_to_discord_cto_chunks(
        _discord_review_posted_message(
            done_label="**CTO PR re-review after autofix done**",
            pr_url=pr_url,
            comment_url=comment_url,
            review_body=review_body,
            cost_suffix=cost_suffix,
            pr_title=pr_title,
        )
    )

    autofix_count = 0
    max_iters = autofix_max_iterations()
    try:
        autofix_count = service.count_autofix_commits(repo=repo, pr_number=pr_number)
    except AutofixError:
        logger.warning("Could not count autofix commits after re-review", exc_info=True)

    jira_final = {"skipped": True, "reason": "not_ready"}
    auto_merge: dict = {"skipped": True, "reason": "not_ready"}
    if ready:
        _post_cto_status(
            f"**Ready to merge**\n{pr_ref}\nComment: {comment_url}"
        )
        issue_key, issue_summary = _jira_issue_context_from_pr(pr)
        auto_merge = _maybe_auto_merge_pr(
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url,
            github_token=gh_token,
            issue_key=issue_key,
            issue_summary=issue_summary,
        )
        jira_final = _final_approval_after_merge(
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url,
            github_token=gh_token,
            merged=bool(auto_merge.get("merged")),
        )
    elif autofix_count >= max_iters:
        _notify_autofix_loop_protection(
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url,
            autofix_count=autofix_count,
            max_iterations=max_iters,
            github_token=gh_token,
        )
    else:
        # autofix_count = number of `[bigas-autofix]` commits on the PR after this push.
        # That is the completed round index (1-based), not "attempts that did nothing".
        completed_round = autofix_count
        _post_to_discord_cto(
            f"**CTO autofix follow-up**\n"
            f"PR still has findings after autofix round "
            f"{completed_round}/{max_iters} "
            f"({autofix_count} `[bigas-autofix]` commit(s) on the PR).\n"
            f"Actions may launch another round if under the limit.\n"
            f"{pr_ref}"
        )

    base.update({
        "finalized": True,
        "rereviewed": True,
        "comment_url": comment_url,
        "ready_to_merge": ready,
        "fixes_pushed": True,
        "head_sha": head_sha,
        "baseline_head_sha": baseline_head_sha,
        "autofix_count": autofix_count,
        "autofix_round": autofix_round_n or autofix_count,
        "max_iterations": max_iters,
        "loop_protection": (not ready) and autofix_count >= max_iters,
        "used_model": review_result.model,
        "usage": review_result.usage_dict(),
        "jira_final_approval": jira_final,
        "auto_merge": auto_merge,
    })
    return _json_summary(base, summarize_followup_result)


@cto_bp.route("/fix_failed_deployment", methods=["POST"])
def fix_failed_deployment():
    """
    Launch a Cursor cloud agent to fix a failed GitHub Actions deploy and open a PR.

    Request JSON:
      - repo (str, required): owner/repo
      - run_id (int, optional): GitHub Actions run ID; used to fetch logs if excerpt omitted
      - workflow (str, optional): workflow filename e.g. deploy-web.yml
      - html_url (str, optional): Actions run URL
      - excerpt (str, optional): failed log excerpt; fetched from GitHub when omitted
      - ref (str, optional): git ref to branch from (default main)
      - conclusion (str, optional): workflow conclusion (default failure)
    """
    from bigas.resources.cto.deploy_hotfix import (
        DeployHotfixError,
        launch_failed_deploy_fix,
    )
    from bigas.resources.devops.service import DevOpsError, get_failed_run_excerpt

    data = request.get_json(silent=True) or {}
    repo = (data.get("repo") or "").strip()
    if not is_owner_repo(repo):
        return _json_summary(
            {"error": "repo is required in the form 'owner/repo'"},
            summarize_deploy_hotfix_result,
            400,
        )

    run_id = data.get("run_id")
    try:
        run_id_int = int(run_id) if run_id is not None and run_id != "" else None
    except (TypeError, ValueError):
        return _json_summary(
            {"error": "run_id must be a valid integer"},
            summarize_deploy_hotfix_result,
            400,
        )

    excerpt = data.get("excerpt")
    if isinstance(excerpt, str):
        excerpt = excerpt.strip()
    else:
        excerpt = ""

    html_url = (data.get("html_url") or "").strip()
    if run_id_int and not html_url:
        html_url = f"https://github.com/{repo}/actions/runs/{run_id_int}"

    if not excerpt and run_id_int:
        try:
            fetched = get_failed_run_excerpt(repo=repo, run_id=run_id_int)
            excerpt = (fetched.get("excerpt") or "").strip()
        except DevOpsError as e:
            return _json_summary(
                {"error": sanitize_error_message(str(e)), "repo": repo},
                summarize_deploy_hotfix_result,
                400,
            )
        except Exception as e:
            logger.exception("Failed to fetch deploy logs")
            return _json_summary(
                {"error": sanitize_error_message(str(e)), "repo": repo},
                summarize_deploy_hotfix_result,
                502,
            )

    if not excerpt:
        return _json_summary(
            {"error": "excerpt or run_id is required so the CTO agent can see the failure"},
            summarize_deploy_hotfix_result,
            400,
        )

    failure = {
        "workflow": (data.get("workflow") or "workflow").strip() or "workflow",
        "run_id": run_id_int,
        "conclusion": (data.get("conclusion") or "failure").strip() or "failure",
        "html_url": html_url,
        "excerpt": excerpt,
    }
    try:
        result = launch_failed_deploy_fix(
            repo=repo,
            failures=[failure],
            starting_ref=(data.get("ref") or "main"),
            cursor_api_key=(data.get("cursor_api_key") or "").strip() or None,
        )
    except DeployHotfixError as e:
        err = sanitize_error_message(str(e))
        logger.warning("Deploy hotfix launch failed: %s", e)
        return _json_summary(
            {"error": err, "repo": repo},
            summarize_deploy_hotfix_result,
            502,
        )

    return _json_summary({"success": True, **result}, summarize_deploy_hotfix_result)


@cto_bp.route("/fetch_ai_usage", methods=["POST"])
def fetch_ai_usage_endpoint():
    """
    Fetch historical AI usage from configured usage providers.

    Request JSON:
      - days (int, default 7, max 90)
      - provider (str, default "all"): all | cursor | llm_logs
      - feature_prefix (str, default "cto_")
      - post_to_discord (bool, default false)
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    try:
        days = int(data.get("days") if data.get("days") is not None else 7)
    except (TypeError, ValueError):
        return jsonify({"error": "days must be an integer"}), 400
    provider = (data.get("provider") or "all").strip() or "all"
    feature_prefix = data.get("feature_prefix")
    if feature_prefix is None:
        feature_prefix = "cto_"
    else:
        feature_prefix = str(feature_prefix)
    post_to_discord = bool(data.get("post_to_discord") or False)

    report = fetch_ai_usage(
        days=days,
        provider=provider,
        feature_prefix=feature_prefix or None,
    )

    if post_to_discord:
        _post_to_discord_cto(format_weekly_cto_ai_report(report))

    # Cap events in HTTP response to keep payloads manageable.
    # Truncation must happen AFTER formatting Discord report so counts are accurate.
    events = report.get("events") or []
    truncated = False
    if len(events) > 200:
        report = {**report, "events": events[:200]}
        truncated = True
        report["events_truncated"] = True

    report["truncated"] = truncated
    return jsonify(report)


@cto_bp.route("/weekly_cto_ai_report", methods=["POST"])
def weekly_cto_ai_report():
    """
    Weekly Bigas AI cost summary from all active usage providers → Discord.

    Includes Cursor autofix plus every LLM feature that emits llm_usage
    (chat, PR review, marketing, Jira, …).

    Request JSON:
      - days (int, default 7)
      - post_to_discord (bool, default true)
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    try:
        days = int(data.get("days") if data.get("days") is not None else 7)
    except (TypeError, ValueError):
        return jsonify({"error": "days must be an integer"}), 400
    post_to_discord = True if data.get("post_to_discord") is None else bool(
        data.get("post_to_discord")
    )

    report = fetch_ai_usage(days=days, provider="all", feature_prefix=None)
    message = format_weekly_cto_ai_report(report)
    if post_to_discord:
        _post_to_discord_cto(message)

    events = report.get("events") or []
    if len(events) > 200:
        report = {**report, "events": events[:200], "events_truncated": True}

    return jsonify(
        {
            "success": True,
            "posted_to_discord": post_to_discord,
            "message": message,
            "report": report,
        }
    )


@cto_bp.route("/run_qa", methods=["POST"])
def run_qa():
    """
    Run automated MCP QA against a target endpoint based on a code diff.

    Request JSON:
      - diff (str, required): git diff or PR diff text
      - mcp_endpoint_url (str, required): base URL of the MCP server to test
      - mcp_auth_token (str, optional): Bearer / access key for the target MCP server
      - pr_url (str, optional): PR URL for context and Discord notifications
      - llm_model (str, optional): override model for planning/evaluation
    """
    data = request.get_json(silent=True) or {}
    diff = data.get("diff")
    raw_mcp_endpoint_url = data.get("mcp_endpoint_url")
    if raw_mcp_endpoint_url is not None and not isinstance(raw_mcp_endpoint_url, str):
        return jsonify({"error": "mcp_endpoint_url must be a string"}), 400
    mcp_endpoint_url = (raw_mcp_endpoint_url or "").strip()
    mcp_auth_token = (data.get("mcp_auth_token") or "").strip() or None
    pr_url = (data.get("pr_url") or "").strip() or None
    llm_model = (data.get("llm_model") or "").strip() or None

    if diff is not None and not isinstance(diff, str):
        return jsonify({"error": "diff must be a string"}), 400
    if not (diff or "").strip():
        return jsonify({"error": "diff is required"}), 400
    if not mcp_endpoint_url:
        return jsonify({"error": "mcp_endpoint_url is required"}), 400

    public_url = (
        (os.environ.get("BIGAS_PUBLIC_URL") or "").strip()
        or (os.environ.get("SERVER_URL") or "").strip()
        or None
    )

    try:
        result = QAAgentService(llm_model=llm_model).run(
            diff=diff,
            mcp_endpoint_url=mcp_endpoint_url,
            mcp_auth_token=mcp_auth_token,
            pr_url=pr_url,
            public_url=public_url,
        )
    except QAAgentError as e:
        err = sanitize_error_message(str(e))
        logger.warning("QA agent failed: %s", e)
        return jsonify({"error": err}), 400
    except Exception as e:
        err = sanitize_error_message(str(e))
        logger.error("QA agent unexpected error", exc_info=True)
        return jsonify({"error": err}), 500

    return jsonify({"success": True, **result})


@cto_bp.route("/website_monitor", methods=["POST"])
def website_monitor():
    """
    Check configured websites for availability and SSL certificate health.

    Reads URLs from MONITOR_URLS environment variable (comma-separated).
    For each URL, performs an HTTP GET request and checks SSL certificate expiry.
    Sends alerts to Discord if any URL is down or has SSL issues.

    Can be triggered by Google Cloud Scheduler on a cron schedule.

    Returns:
        JSON with monitoring results including total URLs checked,
        healthy/unhealthy counts, and whether alerts were sent.
    """
    try:
        result = run_monitoring_checks()
        response = {
            "status": "ok",
            "total_urls": result.total_urls,
            "healthy_count": result.healthy_count,
            "unhealthy_count": result.unhealthy_count,
            "alerts_sent": result.alerts_sent,
            "results": [
                {
                    "url": r.url,
                    "is_healthy": r.is_healthy,
                    "errors": r.errors,
                    "http_status": r.http_status,
                    "ssl_days_until_expiry": r.ssl_days_until_expiry,
                }
                for r in result.results
            ],
        }
        if result.alert_message:
            response["alert_message"] = result.alert_message
        return jsonify(response)
    except Exception as e:
        logger.error("Website monitoring failed", exc_info=True)
        return jsonify({"error": sanitize_error_message(str(e))}), 500


def get_manifest():
    """Return the CTO tools manifest for the combined MCP manifest."""
    return {
        "name": "CTO Tools",
        "description": "Tools for engineering leadership and code review.",
        "tools": [
            {
                "name": "review_and_comment_pr",
                "description": (
                    "Review a pull request diff with AI and post or update a single PR comment on GitHub. "
                    "Pass pr_url (a github.com/.../pull/N link) or repo + pr_number. Diff is optional. "
                    + SUMMARY_REPLY_INSTRUCTION
                ),
                "path": "/mcp/tools/review_and_comment_pr",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository as owner/repo. Optional if pr_url is a GitHub pull request URL.",
                        },
                        "pr_number": {
                            "type": "integer",
                            "description": "Pull request number. Optional if pr_url includes it.",
                        },
                        "pr_url": {
                            "type": "string",
                            "description": "GitHub pull request URL (https://github.com/owner/repo/pull/123). Alternative to repo + pr_number.",
                        },
                        "diff": {
                            "type": "string",
                            "description": "Optional PR diff text. If omitted, Bigas fetches it from GitHub.",
                        },
                        "instructions": {
                            "type": "string",
                            "description": "Optional extra instructions for the reviewer",
                        },
                        "github_token": {
                            "type": "string",
                            "description": "Optional GitHub PAT override (default: GITHUB_TOKEN env)",
                        },
                        "llm_model": {
                            "type": "string",
                            "description": "Optional model override (default: gemini-3.1-pro-preview)",
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "notify_pr_merged",
                "description": (
                    "After a PR is merged (auto-merge or someone else merges), move the "
                    "linked internal-board or Jira ticket to Final approval (manual). "
                    + SUMMARY_REPLY_INSTRUCTION
                ),
                "path": "/mcp/tools/notify_pr_merged",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository as owner/repo. Optional if pr_url is a GitHub pull request URL.",
                        },
                        "pr_number": {
                            "type": "integer",
                            "description": "Pull request number. Optional if pr_url includes it.",
                        },
                        "pr_url": {
                            "type": "string",
                            "description": "GitHub pull request URL. Alternative to repo + pr_number.",
                        },
                        "github_token": {
                            "type": "string",
                            "description": "Optional GitHub PAT override (default: GITHUB_TOKEN env)",
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "autofix_pr",
                "description": (
                    "Launch a Cursor cloud agent to fix actionable findings from the "
                    "Bigas PR review comment on an open pull request. "
                    + SUMMARY_REPLY_INSTRUCTION
                ),
                "path": "/mcp/tools/autofix_pr",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository as owner/repo. Optional if pr_url is a GitHub pull request URL.",
                        },
                        "pr_number": {
                            "type": "integer",
                            "description": "Pull request number. Optional if pr_url includes it.",
                        },
                        "pr_url": {
                            "type": "string",
                            "description": "GitHub pull request URL. Alternative to repo + pr_number.",
                        },
                        "force": {
                            "type": "boolean",
                            "description": "Bypass clean-review and autofix-loop guards",
                        },
                        "review_body": {
                            "type": "string",
                            "description": "Optional review text override",
                        },
                        "github_token": {
                            "type": "string",
                            "description": "Optional GitHub PAT override",
                        },
                        "cursor_api_key": {
                            "type": "string",
                            "description": "Optional Cursor API key override",
                        },
                        "is_retry": {
                            "type": "boolean",
                            "description": "Suppress Discord notifications on retry polls (for cooldown loops)",
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "fix_failed_deployment",
                "description": (
                    "Launch a Cursor cloud agent to fix a failed GitHub Actions deploy "
                    "and open a PR. Pass repo plus run_id (logs are fetched) or excerpt. "
                    + SUMMARY_REPLY_INSTRUCTION
                ),
                "path": "/mcp/tools/fix_failed_deployment",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository as owner/repo (required)",
                        },
                        "run_id": {
                            "type": "integer",
                            "description": "Failed GitHub Actions run ID. Used to fetch logs if excerpt is omitted.",
                        },
                        "workflow": {
                            "type": "string",
                            "description": "Workflow filename e.g. deploy-web.yml",
                        },
                        "html_url": {
                            "type": "string",
                            "description": "GitHub Actions run URL",
                        },
                        "excerpt": {
                            "type": "string",
                            "description": "Failed log excerpt. Optional if run_id is provided.",
                        },
                        "ref": {
                            "type": "string",
                            "description": "Git ref to branch from (default: main)",
                        },
                    },
                    "required": ["repo"],
                },
            },
            {
                "name": "autofix_followup",
                "description": (
                    "Poll Cursor autofix status; when finished, post Discord updates "
                    "and re-review the PR (ready-to-merge when clean). "
                    "Pass pr_url or repo + pr_number, and agent_id or a cursor.com/agents URL. "
                    + SUMMARY_REPLY_INSTRUCTION
                ),
                "path": "/mcp/tools/autofix_followup",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository as owner/repo. Optional if pr_url is a GitHub pull request URL.",
                        },
                        "pr_number": {
                            "type": "integer",
                            "description": "Pull request number. Optional if pr_url includes it.",
                        },
                        "pr_url": {
                            "type": "string",
                            "description": "GitHub pull request URL. Alternative to repo + pr_number.",
                        },
                        "agent_id": {
                            "type": "string",
                            "description": "Cursor cloud agent id (bc-...) or a cursor.com/agents/bc-... URL",
                        },
                        "run_id": {
                            "type": "string",
                            "description": "Optional Cursor run id",
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "fetch_ai_usage",
                "description": (
                    "Fetch historical AI usage from configured usage providers "
                    "(Cursor cloud agents API and/or LLM Cloud Logging). "
                    "Returns list-price estimates and token totals."
                ),
                "path": "/mcp/tools/fetch_ai_usage",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "Lookback window in days (default 7, max 90)",
                        },
                        "provider": {
                            "type": "string",
                            "description": 'Usage provider: "all" (default), "cursor", or "llm_logs"',
                        },
                        "feature_prefix": {
                            "type": "string",
                            "description": 'Optional feature filter prefix (default "cto_")',
                        },
                        "post_to_discord": {
                            "type": "boolean",
                            "description": "Post a summary to the CTO Discord channel",
                        },
                    },
                },
            },
            {
                "name": "weekly_cto_ai_report",
                "description": (
                    "Weekly Bigas AI cost summary across active usage providers "
                    "(Cursor autofix + LLM usage from Cloud Logging: chat, "
                    "PR review, marketing, and other features). "
                    "Posts to Discord by default — suitable for Cloud Scheduler."
                ),
                "path": "/mcp/tools/weekly_cto_ai_report",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "Lookback window in days (default 7)",
                        },
                        "post_to_discord": {
                            "type": "boolean",
                            "description": "Post to CTO Discord (default true)",
                        },
                    },
                },
            },
            {
                "name": "website_monitor",
                "description": (
                    "Check configured websites for availability and SSL certificate health. "
                    "Reads URLs from MONITOR_URLS env var. Sends Discord alerts on failures. "
                    "Ideal for Cloud Scheduler cron triggers."
                ),
                "path": "/mcp/tools/website_monitor",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "run_qa",
                "description": (
                    "Analyze a code diff, exercise relevant MCP tools on a target server, "
                    "and evaluate output quality. Posts to Discord/Jira when improvements "
                    "or new features are suggested."
                ),
                "path": "/mcp/tools/run_qa",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "diff": {
                            "type": "string",
                            "description": "Git or PR diff text (required)",
                        },
                        "mcp_endpoint_url": {
                            "type": "string",
                            "description": "Base URL of the MCP server to test (required)",
                        },
                        "mcp_auth_token": {
                            "type": "string",
                            "description": "Optional Bearer/access key for the target MCP server",
                        },
                        "pr_url": {
                            "type": "string",
                            "description": "Optional PR URL for notifications",
                        },
                        "llm_model": {
                            "type": "string",
                            "description": "Optional LLM model override",
                        },
                    },
                    "required": ["diff", "mcp_endpoint_url"],
                },
            },
        ],
    }
