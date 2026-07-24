"""
CTO resource endpoints: PR review and comment (and future CTO tools).
"""
from __future__ import annotations

import logging
import os

import requests
from flask import Blueprint, jsonify, request

from bigas.resources.cto.autofix.heuristics import (
    latest_commit_is_autofix,
    review_is_ready_to_merge,
)
from bigas.resources.cto.autofix.service import (
    AutofixError,
    AutofixService,
    autofix_looks_like_confirmation_stop,
)
from bigas.resources.cto.pr_review.github_client import (
    BIGAS_REVIEW_MARKER,
    GitHubPRCommentClient,
    GitHubPRCommentError,
)
from bigas.resources.cto.pr_review.service import PRReviewError, PRReviewService
from bigas.resources.marketing.utils import sanitize_error_message

cto_bp = Blueprint(
    "cto_bp",
    __name__,
    url_prefix="/mcp/tools",
)

logger = logging.getLogger(__name__)

# Max characters for a GitHub comment to avoid API errors.
# The official limit is 65,536 bytes, so 60k chars is a safe buffer.
MAX_GITHUB_COMMENT_CHARS = 60_000


def _post_to_discord_cto(message: str) -> None:
    """Post to CTO Discord channel if DISCORD_WEBHOOK_URL_CTO is set (e.g. from Secret Manager).
    Callers must pass only sanitized messages (use sanitize_error_message for errors) to avoid leaking tokens.
    """
    webhook = (os.environ.get("DISCORD_WEBHOOK_URL_CTO") or "").strip()
    if not webhook or webhook.startswith("placeholder"):
        logger.info("DISCORD_WEBHOOK_URL_CTO not set or placeholder, skipping Discord post")
        return
    if len(message) > 2000:
        message = message[:1997] + "..."
    try:
        resp = requests.post(webhook, json={"content": message}, timeout=20)
        if resp.status_code != 204:
            logger.warning("CTO Discord post failed: %s %s", resp.status_code, resp.text[:200])
        else:
            logger.info("CTO Discord post succeeded")
    except Exception:
        logger.warning("CTO Discord post failed", exc_info=True)


def _post_to_discord_cto_chunks(message: str, *, chunk_size: int = 1900) -> None:
    """Post long content to CTO Discord in multiple messages. Discord limit 2000 chars; we use chunk_size margin."""
    webhook = (os.environ.get("DISCORD_WEBHOOK_URL_CTO") or "").strip()
    if not webhook or webhook.startswith("placeholder"):
        return
    if not message:
        return
    msg = message.strip()
    if len(msg) <= 2000:
        _post_to_discord_cto(msg)
        return
    start = 0
    while start < len(msg):
        end = min(start + chunk_size, len(msg))
        nl = msg.rfind("\n", start, end)
        if nl > start + 200:
            end = nl
        _post_to_discord_cto(msg[start:end].strip())
        start = end


@cto_bp.route("/review_and_comment_pr", methods=["POST"])
def review_and_comment_pr():
    """
    Review a pull request diff with AI (Codex) and post or update a single PR comment.

    Request JSON:
      - repo (str, required): "owner/repo"
      - pr_number (int, required): pull request number
      - diff (str, required): PR diff text
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
    # Post "review started" immediately so Discord is always notified when the endpoint is hit
    repo_raw = (data.get("repo") or "").strip() or "?"
    pr_raw = data.get("pr_number", "?")
    if phase == "post_autofix":
        _post_to_discord_cto(
            f"**CTO PR re-review after autofix started**\nPR: https://github.com/{repo_raw}/pull/{pr_raw}"
        )
    else:
        _post_to_discord_cto(
            f"**CTO PR review started**\nPR: https://github.com/{repo_raw}/pull/{pr_raw}"
        )

    repo = (data.get("repo") or "").strip()
    pr_number = data.get("pr_number")
    diff = data.get("diff")
    instructions = (data.get("instructions") or "").strip() or None
    github_token = (data.get("github_token") or "").strip() or os.environ.get("GITHUB_TOKEN") or ""
    llm_model = (data.get("llm_model") or "").strip() or None

    if not repo:
        _post_to_discord_cto("**CTO PR review done**\nNo comment posted.\nReason: repo is required.")
        return jsonify({"error": "repo is required (e.g. 'owner/repo')"}), 400
    if "/" not in repo or repo.count("/") != 1:
        _post_to_discord_cto("**CTO PR review done**\nNo comment posted.\nReason: repo must be owner/repo.")
        return jsonify({"error": "repo must be in the form 'owner/repo'"}), 400
    if pr_number is None:
        _post_to_discord_cto("**CTO PR review done**\nNo comment posted.\nReason: pr_number is required.")
        return jsonify({"error": "pr_number is required"}), 400
    try:
        pr_number = int(pr_number)
    except (TypeError, ValueError):
        _post_to_discord_cto("**CTO PR review done**\nNo comment posted.\nReason: pr_number must be an integer.")
        return jsonify({"error": "pr_number must be an integer"}), 400
    if pr_number < 1:
        _post_to_discord_cto("**CTO PR review done**\nNo comment posted.\nReason: pr_number must be positive.")
        return jsonify({"error": "pr_number must be a positive integer"}), 400
    if diff is None:
        _post_to_discord_cto("**CTO PR review done**\nNo comment posted.\nReason: diff is required.")
        return jsonify({"error": "diff is required"}), 400
    if not isinstance(diff, str):
        _post_to_discord_cto("**CTO PR review done**\nNo comment posted.\nReason: diff must be a string.")
        return jsonify({"error": "diff must be a string"}), 400
    if not github_token:
        _post_to_discord_cto(
            "**CTO PR review done**\nNo comment posted.\nReason: GitHub token is required (GITHUB_TOKEN or github_token)."
        )
        return (
            jsonify(
                {
                    "error": "GitHub token is required. Set GITHUB_TOKEN in env or pass github_token in the request."
                }
            ),
            400,
        )

    owner, repo_name = repo.split("/", 1)
    pr_url = f"https://github.com/{repo}/pull/{pr_number}"

    try:
        review_service = PRReviewService(openai_model=llm_model)
        review_body = review_service.review(diff=diff, instructions=instructions)
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

    return jsonify({
        "success": True,
        "comment_url": comment_url,
        "review_posted": bool(comment_url),
        "used_model": review_service._model,
        "ready_to_merge": ready,
        "phase": phase,
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
    """
    data = request.get_json(silent=True) or {}
    repo = (data.get("repo") or "").strip()
    pr_number = data.get("pr_number")
    force = bool(data.get("force") or False)
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
    _post_to_discord_cto(f"**CTO autofix started**\nPR: {pr_url}")

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
        _post_to_discord_cto(
            f"**CTO autofix done**\nSkipped.\nReason: {sanitize_error_message(reason)}\nPR: {pr_url}"
        )
        return jsonify({"success": True, **result})

    agent_url = result.get("agent_url") or ""
    agent_id = result.get("agent_id") or ""
    _post_to_discord_cto(
        f"**CTO autofix launched**\nPR: {pr_url}\nAgent: {agent_url or agent_id}"
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

    _post_to_discord_cto(f"**CTO PR re-review after autofix started**\nPR: {pr_url}")
    try:
        review_service = PRReviewService()
        review_body = review_service.review(diff=diff)
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

    gh_token = github_token or os.environ.get("GITHUB_TOKEN") or ""
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
    jira_final = {"skipped": True, "reason": "not_ready"}
    if ready:
        _post_to_discord_cto(
            f"**Ready to merge**\nPR: {pr_url}\nComment: {comment_url}"
        )
        try:
            from bigas.resources.product.jira_automation.final_approval import (
                transition_issue_to_final_approval_for_pr,
            )

            jira_final = transition_issue_to_final_approval_for_pr(
                repo=repo,
                pr_number=pr_number,
                pr_url=pr_url,
                github_token=gh_token,
            )
        except Exception:
            logger.warning("Jira final-approval hook failed", exc_info=True)
            jira_final = {"ok": False, "error": "final_approval_hook_exception"}
    else:
        _post_to_discord_cto(
            f"**CTO autofix follow-up**\nPR still has findings after autofix.\nPR: {pr_url}"
        )

    base.update({
        "finalized": True,
        "rereviewed": True,
        "comment_url": comment_url,
        "ready_to_merge": ready,
        "fixes_pushed": True,
        "used_model": review_service._model,
        "jira_final_approval": jira_final,
    })
    return jsonify(base)


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
        ],
    }
