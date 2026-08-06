"""
CTO resource endpoints: PR review and comment (and future CTO tools).
"""
from __future__ import annotations

import logging
import os

import requests
from flask import Blueprint, jsonify, request

from bigas.resources.cto.autofix.heuristics import (
    autofix_max_iterations,
    format_loop_protection_message,
    latest_commit_is_autofix,
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
    GitHubPRCommentClient,
    GitHubPRCommentError,
)
from bigas.resources.cto.pr_review.service import PRReviewError, PRReviewService
from bigas.discord_webhook import post_long_to_discord, post_to_discord
from bigas.resources.marketing.utils import sanitize_error_message
from bigas.providers.monitoring.service import MonitoringService, run_monitoring_checks

cto_bp = Blueprint(
    "cto_bp",
    __name__,
    url_prefix="/mcp/tools",
)

logger = logging.getLogger(__name__)

# Max characters for a GitHub comment to avoid API errors.


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
    *, repo: str, pr_number: int, pr_url: str, github_token: str
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
        )
    except Exception:
        logger.warning("Jira final-approval hook failed", exc_info=True)
        return {"ok": False, "error": "final_approval_hook_exception"}


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
    msg = (
        f"**CTO autofix stopped (loop protection)**\n"
        f"{detail}\n"
        f"PR: {pr_url}"
    )
    _post_to_discord_cto(msg)
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

        token = (github_token or os.environ.get("GITHUB_TOKEN") or "").strip()
        if not token or "/" not in repo:
            return
        owner, name = repo.split("/", 1)

        resp = requests.get(
            f"https://api.github.com/repos/{owner}/{name}/pulls/{pr_number}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return
        pr = resp.json() if resp.text else {}
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


def _post_to_discord_cto(message: str) -> None:
    """Post to CTO Discord channel if DISCORD_WEBHOOK_URL_CTO is set (e.g. from Secret Manager).
    Callers must pass only sanitized messages (use sanitize_error_message for errors) to avoid leaking tokens.
    """
    webhook = _cto_discord_webhook()
    if not webhook:
        logger.info("DISCORD_WEBHOOK_URL_CTO not set or placeholder, skipping Discord post")
        return
    post_to_discord(webhook, message)


def _post_to_discord_cto_chunks(message: str) -> None:
    """Post long CTO content via the shared Discord long-message helper."""
    webhook = _cto_discord_webhook()
    if not webhook or not (message or "").strip():
        return
    post_long_to_discord(webhook, message.strip())


@cto_bp.route("/review_and_comment_pr", methods=["POST"])
def review_and_comment_pr():
    """
    Review a pull request diff with AI (Codex) and post or update a single PR comment.

    Request JSON:
      - repo (str, required): "owner/repo"
      - pr_number (int, required): pull request number
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

    repo = (data.get("repo") or "").strip()
    pr_number = data.get("pr_number")
    diff = data.get("diff")
    instructions = (data.get("instructions") or "").strip() or None
    github_token = (data.get("github_token") or "").strip() or os.environ.get("GITHUB_TOKEN") or ""
    llm_model = (data.get("llm_model") or "").strip() or None
    repo_display = repo or "?"
    pr_display = pr_number if pr_number is not None else "?"

    def _fail(reason: str, status: int, error: str | None = None):
        _post_to_discord_cto(
            "**CTO PR review done**\n"
            "No comment posted.\n"
            f"PR: https://github.com/{repo_display}/pull/{pr_display}\n"
            f"Reason: {reason}"
        )
        return jsonify({"error": error or reason}), status

    if not repo:
        return _fail("repo is required.", 400, "repo is required (e.g. 'owner/repo')")
    if "/" not in repo or repo.count("/") != 1:
        return _fail("repo must be owner/repo.", 400, "repo must be in the form 'owner/repo'")
    if pr_number is None:
        return _fail("pr_number is required.", 400)
    try:
        pr_number = int(pr_number)
    except (TypeError, ValueError):
        return _fail("pr_number must be an integer.", 400)
    if pr_number < 1:
        return _fail("pr_number must be positive.", 400, "pr_number must be a positive integer")
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
            f"**CTO PR re-review after autofix started**\nPR: https://github.com/{repo}/pull/{pr_number}"
        )
    else:
        _post_to_discord_cto(
            f"**CTO PR review started**\nPR: https://github.com/{repo}/pull/{pr_number}"
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
        review_body = review_service.review(
            diff=diff,
            instructions=instructions,
            phase=phase,  # type: ignore[arg-type]
            previous_review=previous_review,
        )
    except PRReviewError as e:
        logger.warning("PR review failed: %s", e)
        _post_to_discord_cto(f"**CTO PR review done**\nNo comment posted.\nReason: {sanitize_error_message(str(e))}")
        return jsonify({"error": sanitize_error_message(str(e))}), 500

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
        _post_to_discord_cto(f"**CTO PR review done**\nNo comment posted.\nReason: {err_msg}")
        if "401" in str(e) or "invalid" in str(e).lower() or "expired" in str(e).lower():
            return jsonify({"error": err_msg}), 401
        if "403" in str(e):
            return jsonify({"error": err_msg}), 403
        if "404" in str(e) or "not found" in str(e).lower():
            return jsonify({"error": err_msg}), 404
        return jsonify({"error": err_msg}), 502

    ready = review_is_ready_to_merge(review_body)
    done_label = (
        "**CTO PR re-review after autofix done**"
        if phase == "post_autofix"
        else "**CTO PR review done**"
    )
    if comment_url:
        _post_to_discord_cto(f"{done_label}\nComment posted: {comment_url}\n\n---\n\n**Review:**")
        _post_to_discord_cto_chunks(review_body)
    else:
        _post_to_discord_cto(f"{done_label}\nNo comment posted. (No URL returned from GitHub.)")

    if ready:
        _post_to_discord_cto(
            f"**Ready to merge**\nPR: {pr_url}\n"
            + (f"Comment: {comment_url}" if comment_url else "")
        )
        jira_final = _jira_final_approval_for_pr(
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url,
            github_token=github_token,
        )
    else:
        jira_final = {"skipped": True, "reason": "not_ready"}

    return jsonify({
        "success": True,
        "comment_url": comment_url,
        "review_posted": bool(comment_url),
        "used_model": review_service._model,
        "ready_to_merge": ready,
        "phase": phase,
        "jira_final_approval": jira_final,
    })


@cto_bp.route("/autofix_pr", methods=["POST"])
def autofix_pr():
    """
    Launch a Cursor cloud agent to fix actionable findings from the Bigas PR review.

    Request JSON:
      - repo (str, required): "owner/repo"
      - pr_number (int, required)
      - force (bool, optional): bypass clean-review / autofix-loop guards
      - review_body (str, optional): override; else fetch Bigas-marked PR comment
      - github_token (str, optional): override GITHUB_TOKEN
      - cursor_api_key (str, optional): override CURSOR_API_KEY
      - is_retry (bool, optional): suppress Discord skip notifications on Actions
          cooldown/retry polls (cooldown itself never posts Discord)
    """
    data = request.get_json(silent=True) or {}
    repo = (data.get("repo") or "").strip()
    pr_number = data.get("pr_number")
    force = bool(data.get("force") or False)
    is_retry = bool(data.get("is_retry") or False)
    review_body = data.get("review_body")
    if isinstance(review_body, str):
        review_body = review_body.strip() or None
    else:
        review_body = None
    github_token = (data.get("github_token") or "").strip() or None
    cursor_api_key = (data.get("cursor_api_key") or "").strip() or None

    if not repo or "/" not in repo or repo.count("/") != 1:
        return jsonify({"error": "repo is required in the form 'owner/repo'"}), 400
    if pr_number is None:
        return jsonify({"error": "pr_number is required"}), 400
    try:
        pr_number = int(pr_number)
    except (TypeError, ValueError):
        return jsonify({"error": "pr_number must be an integer"}), 400
    if pr_number < 1:
        return jsonify({"error": "pr_number must be a positive integer"}), 400

    pr_url = f"https://github.com/{repo}/pull/{pr_number}"

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
        _post_to_discord_cto(f"**CTO autofix done**\nNot launched.\nReason: {err}")
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
        return jsonify({"error": err}), status

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
                f"PR: {pr_url}"
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
                    f"PR: {pr_url}"
                )
        elif not is_retry:
            _post_to_discord_cto(
                f"**CTO autofix skipped**\n"
                f"Reason: {sanitize_error_message(reason)}\nPR: {pr_url}"
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
        return jsonify({"success": True, **result})

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
        f"PR: {pr_url}\nAgent: {agent_url or agent_id}"
    )
    return jsonify({"success": True, **result})


@cto_bp.route("/autofix_followup", methods=["POST"])
def autofix_followup():
    """
    Poll Cursor autofix status; optionally finalize (Discord + re-review) once done.

    Request JSON:
      - repo, pr_number, agent_id (required)
      - run_id (optional)
      - finalize (bool, default false): if true and agent terminal, Discord + re-review
    """
    data = request.get_json(silent=True) or {}
    repo = (data.get("repo") or "").strip()
    pr_number = data.get("pr_number")
    agent_id = (data.get("agent_id") or "").strip()
    run_id = (data.get("run_id") or "").strip() or None
    finalize = bool(data.get("finalize") or False)
    github_token = (data.get("github_token") or "").strip() or None
    cursor_api_key = (data.get("cursor_api_key") or "").strip() or None

    if not repo or "/" not in repo or repo.count("/") != 1:
        return jsonify({"error": "repo is required in the form 'owner/repo'"}), 400
    if pr_number is None:
        return jsonify({"error": "pr_number is required"}), 400
    try:
        pr_number = int(pr_number)
    except (TypeError, ValueError):
        return jsonify({"error": "pr_number must be an integer"}), 400
    if pr_number < 1:
        return jsonify({"error": "pr_number must be a positive integer"}), 400
    if not agent_id:
        return jsonify({"error": "agent_id is required"}), 400

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
        return jsonify({"error": err}), 502

    base = {
        "success": True,
        "done": bool(status.get("done")),
        "ok": bool(status.get("ok")),
        "status": status.get("status"),
        "agent_id": status.get("agent_id"),
        "run_id": status.get("run_id"),
        "agent_url": status.get("agent_url"),
        "pr_url": pr_url,
        "finalized": False,
    }

    if not status.get("done") or not finalize:
        return jsonify(base)

    agent_url = status.get("agent_url") or ""
    run_status = status.get("status") or "UNKNOWN"
    if not status.get("ok"):
        _post_to_discord_cto(
            f"**CTO autofix failed**\nPR: {pr_url}\n"
            f"Status: {run_status}\nAgent: {agent_url}"
        )
        base.update({"finalized": True, "ready_to_merge": False, "fixes_pushed": False})
        return jsonify(base)

    fixes_pushed = False
    head_sha = ""
    try:
        head_sha, head_message = service.get_pr_head_commit(
            repo=repo, pr_number=pr_number
        )
        fixes_pushed = latest_commit_is_autofix(head_message)
    except AutofixError as e:
        logger.warning("Could not read PR head after autofix: %s", e)

    result_text = status.get("result_text") or ""
    asked_confirm = autofix_looks_like_confirmation_stop(result_text)
    if not fixes_pushed:
        why = (
            "Agent appears to have stopped to ask for confirmation."
            if asked_confirm
            else "No `[bigas-autofix]` commit on PR head (nothing pushed, or agent only proposed changes)."
        )
        _post_to_discord_cto(
            f"**CTO autofix finished without commits**\nPR: {pr_url}\n"
            f"{why}\nAgent: {agent_url}\n"
            f"Re-run autofix or continue the agent manually."
        )
        base.update(
            {
                "finalized": True,
                "ready_to_merge": False,
                "fixes_pushed": False,
                "asked_confirmation": asked_confirm,
                "head_sha": head_sha,
                "rereviewed": False,
            }
        )
        return jsonify(base)

    _post_to_discord_cto(
        f"**CTO autofix completed**\nPR: {pr_url}\n"
        f"Fixes pushed to the PR branch.\nAgent: {agent_url}"
    )

    try:
        diff = service.fetch_pr_diff(repo=repo, pr_number=pr_number)
    except AutofixError as e:
        err = sanitize_error_message(str(e))
        _post_to_discord_cto(
            f"**CTO PR re-review after autofix done**\nNo comment posted.\nReason: {err}"
        )
        return jsonify({"error": err, **base, "finalized": True, "rereviewed": False}), 502

    gh_token = github_token or os.environ.get("GITHUB_TOKEN") or ""
    _post_to_discord_cto(f"**CTO PR re-review after autofix started**\nPR: {pr_url}")
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
        review_body = review_service.review(
            diff=diff,
            phase="post_autofix",
            previous_review=previous_review,
        )
    except PRReviewError as e:
        err = sanitize_error_message(str(e))
        _post_to_discord_cto(
            f"**CTO PR re-review after autofix done**\nNo comment posted.\nReason: {err}"
        )
        return jsonify({"error": err, **base, "finalized": True, "rereviewed": False}), 500

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
        _post_to_discord_cto(
            f"**CTO PR re-review after autofix done**\nNo comment posted.\nReason: {err}"
        )
        return jsonify({"error": err, **base, "finalized": True, "rereviewed": False}), 502

    ready = review_is_ready_to_merge(review_body)
    _post_to_discord_cto(
        f"**CTO PR re-review after autofix done**\nComment posted: {comment_url}\n\n---\n\n**Review:**"
    )
    _post_to_discord_cto_chunks(review_body)

    autofix_count = 0
    max_iters = autofix_max_iterations()
    try:
        autofix_count = service.count_autofix_commits(repo=repo, pr_number=pr_number)
    except AutofixError:
        logger.warning("Could not count autofix commits after re-review", exc_info=True)

    jira_final = {"skipped": True, "reason": "not_ready"}
    if ready:
        _post_to_discord_cto(
            f"**Ready to merge**\nPR: {pr_url}\nComment: {comment_url}"
        )
        jira_final = _jira_final_approval_for_pr(
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url,
            github_token=gh_token,
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
        _post_to_discord_cto(
            f"**CTO autofix follow-up**\n"
            f"PR still has findings after autofix round "
            f"{autofix_count}/{max_iters}.\n"
            f"Another autofix round may run if under the limit.\n"
            f"PR: {pr_url}"
        )

    base.update({
        "finalized": True,
        "rereviewed": True,
        "comment_url": comment_url,
        "ready_to_merge": ready,
        "fixes_pushed": True,
        "autofix_count": autofix_count,
        "max_iterations": max_iters,
        "loop_protection": (not ready) and autofix_count >= max_iters,
        "used_model": review_service._model,
        "jira_final_approval": jira_final,
    })
    return jsonify(base)


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
                "description": "Review a pull request diff with AI (Codex) and post or update a single PR comment on GitHub.",
                "path": "/mcp/tools/review_and_comment_pr",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository in the form 'owner/repo' (required)",
                        },
                        "pr_number": {
                            "type": "integer",
                            "description": "Pull request number (required)",
                        },
                        "diff": {
                            "type": "string",
                            "description": "PR diff text (required)",
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
                    "required": ["repo", "pr_number", "diff"],
                },
            },
            {
                "name": "autofix_pr",
                "description": (
                    "Launch a Cursor cloud agent to fix actionable findings from the "
                    "Bigas PR review comment on an open pull request."
                ),
                "path": "/mcp/tools/autofix_pr",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository in the form 'owner/repo' (required)",
                        },
                        "pr_number": {
                            "type": "integer",
                            "description": "Pull request number (required)",
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
                    "required": ["repo", "pr_number"],
                },
            },
            {
                "name": "autofix_followup",
                "description": (
                    "Poll Cursor autofix status; when finished, post Discord updates "
                    "and re-review the PR (ready-to-merge when clean)."
                ),
                "path": "/mcp/tools/autofix_followup",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository in the form 'owner/repo' (required)",
                        },
                        "pr_number": {
                            "type": "integer",
                            "description": "Pull request number (required)",
                        },
                        "agent_id": {
                            "type": "string",
                            "description": "Cursor cloud agent id (bc-...)",
                        },
                        "run_id": {
                            "type": "string",
                            "description": "Optional Cursor run id",
                        },
                    },
                    "required": ["repo", "pr_number", "agent_id"],
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
        ],
    }
