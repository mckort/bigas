"""
CTO resource endpoints: PR review and comment (and future CTO tools).
"""
from __future__ import annotations

import logging
import os

import requests
from flask import Blueprint, jsonify, request

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


def _post_to_discord_cto(message: str) -> None:
    """Post to CTO Discord channel if DISCORD_WEBHOOK_URL_CTO is set (e.g. from Secret Manager)."""
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
      - llm_model (str, optional): override model for this request (default: gpt-4o)

    Returns:
      - success, comment_url, review_posted; or error with status 4xx/5xx.
    """
    data = request.get_json(silent=True) or {}
    # Post "review started" immediately so Discord is always notified when the endpoint is hit
    repo_raw = (data.get("repo") or "").strip() or "?"
    pr_raw = data.get("pr_number", "?")
    _post_to_discord_cto(f"**CTO PR review started**\nPR: https://github.com/{repo_raw}/pull/{pr_raw}")

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

    if comment_url:
        _post_to_discord_cto(f"**CTO PR review done**\nComment posted: {comment_url}")
    else:
        _post_to_discord_cto("**CTO PR review done**\nNo comment posted. (No URL returned from GitHub.)")

    return jsonify({
        "success": True,
        "comment_url": comment_url,
        "review_posted": bool(comment_url),
        "used_model": review_service._model,
    })


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
                            "description": "Optional model override (default: gpt-4o)",
                        },
                    },
                    "required": ["repo", "pr_number", "diff"],
                },
            },
        ],
    }
