"""Human-in-the-loop HTTP routes for weekly X posts."""
from __future__ import annotations

import logging

from flask import Blueprint, Response, request

from bigas.resources.product.x_posts.html import (
    error_page,
    partial_success_page,
    preview_page,
    success_page,
)
from bigas.resources.product.x_posts.service import XPostsError, XPostsService
from bigas.resources.product.x_posts.signing import verify_draft_token

logger = logging.getLogger(__name__)

x_posts_bp = Blueprint("x_posts_bp", __name__, url_prefix="/api/x-posts")


def _service() -> XPostsService:
    return XPostsService()


def _html(body: str, status: int = 200) -> Response:
    return Response(body, status=status, mimetype="text/html; charset=utf-8")


def _format_post_result(result: dict) -> str:
    lines: list[str] = []
    posted = result.get("posted") or []
    failed = result.get("failed") or []
    if posted:
        lines.append("Successfully posted:")
        for item in posted:
            lines.append(f"  @{item.get('account', '?')}:")
            for url in item.get("urls") or []:
                lines.append(f"    {url}")
    if failed:
        lines.append("")
        lines.append("Failed:")
        for item in failed:
            account = item.get("account", "?")
            error = item.get("error") or "Unknown error"
            lines.append(f"  @{account}: {error}")
            partial_urls = item.get("posted_urls") or []
            if partial_urls:
                lines.append(
                    "  Partial thread — these tweets were published before the failure:"
                )
                for url in partial_urls:
                    lines.append(f"    {url}")
    return "\n".join(lines).strip()


def _require_token(draft_id: str) -> str | None:
    token = (request.args.get("token") or request.form.get("token") or "").strip()
    if not verify_draft_token(draft_id, token):
        return None
    return token


@x_posts_bp.route("/<draft_id>", methods=["GET"])
def review_x_post(draft_id: str):
    token = _require_token(draft_id)
    if token is None:
        return _html(error_page(title="Invalid link", message="This approval link is invalid."), 403)
    try:
        draft = _service().load_draft(draft_id)
    except XPostsError as e:
        status = 404 if "not found" in str(e).lower() or "expired" in str(e).lower() else 400
        return _html(error_page(title="Draft unavailable", message=str(e)), status)
    except Exception:
        logger.error("Failed to load X post draft %s", draft_id, exc_info=True)
        return _html(error_page(title="Error", message="Could not load this draft."), 500)
    action_base = f"/api/x-posts/{draft_id}"
    return _html(preview_page(draft, action_base=action_base, token=token))


@x_posts_bp.route("/<draft_id>/approve", methods=["POST"])
def approve_x_post(draft_id: str):
    if _require_token(draft_id) is None:
        return _html(error_page(title="Invalid link", message="This approval link is invalid."), 403)
    try:
        edited = [str(t) for t in request.form.getlist("tweets")]
        result = _service().approve(draft_id, tweets=edited)
    except XPostsError as e:
        status = 404 if "not found" in str(e).lower() or "expired" in str(e).lower() else 400
        return _html(error_page(title="Could not post", message=str(e)), status)
    except Exception:
        logger.error("Failed to approve X post %s", draft_id, exc_info=True)
        return _html(error_page(title="Error", message="Publishing to X failed."), 500)

    details = _format_post_result(result)
    posted = result.get("posted") or []
    failed = result.get("failed") or []
    if result.get("ok"):
        return _html(
            success_page(
                title="Posted to X",
                message="The draft was published and removed from storage.",
                extra=details,
            )
        )
    if posted or any(item.get("posted_urls") for item in failed):
        return _html(
            partial_success_page(
                title="Partially posted to X",
                message=(
                    "Some posts were published before an error occurred. "
                    "The draft was removed from storage to prevent duplicate posts. "
                    "Review the details below before posting again manually."
                ),
                extra=details,
            )
        )
    first_error = (failed[0].get("error") if failed else None) or "Publishing to X failed."
    return _html(
        error_page(
            title="Could not post",
            message=first_error,
            extra=details,
        ),
        502,
    )


@x_posts_bp.route("/<draft_id>/decline", methods=["POST"])
def decline_x_post(draft_id: str):
    if _require_token(draft_id) is None:
        return _html(error_page(title="Invalid link", message="This approval link is invalid."), 403)
    try:
        _service().decline(draft_id)
    except XPostsError as e:
        status = 404 if "not found" in str(e).lower() else 400
        return _html(error_page(title="Could not decline", message=str(e)), status)
    except Exception:
        logger.error("Failed to decline X post %s", draft_id, exc_info=True)
        return _html(error_page(title="Error", message="Could not delete this draft."), 500)
    return _html(
        success_page(
            title="Draft declined",
            message=(
                "The stored draft was deleted. Nothing was posted to X. "
                "You can still copy the Discord proposal and post manually."
            ),
        )
    )
