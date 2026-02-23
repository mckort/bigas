from flask import Blueprint, jsonify, request
import logging
import os
import requests

from bigas.resources.marketing.utils import sanitize_error_message, validate_request_data
from bigas.resources.product.create_release_notes.service import CreateReleaseNotesService, ReleaseNotesError
from bigas.resources.product.progress_updates.service import ProgressUpdatesService, ProgressUpdatesError

product_bp = Blueprint(
    'product_bp', __name__,
    url_prefix='/mcp/tools'
)

logger = logging.getLogger(__name__)


def _post_to_discord(webhook_url: str, message: str) -> None:
    if not webhook_url or webhook_url.strip() == "" or webhook_url.startswith("placeholder") or webhook_url == "placeholder":
        return

    if len(message) > 2000:
        message = message[:1997] + "..."

    try:
        resp = requests.post(webhook_url, json={"content": message}, timeout=20)
        # Discord returns 204 No Content on success
        if resp.status_code != 204:
            logger.error(f"Failed to post to Discord: {resp.status_code} {resp.text[:300]}")
    except Exception:
        logger.error("Failed to post to Discord", exc_info=True)


def _post_to_discord_in_chunks(webhook_url: str, message: str, *, chunk_size: int = 1900) -> None:
    """
    Post long content to Discord by splitting into multiple messages.
    Discord hard limit is 2000 chars; we keep a margin.
    """
    if not message:
        return
    msg = message.strip()
    if len(msg) <= 2000:
        _post_to_discord(webhook_url, msg)
        return

    start = 0
    while start < len(msg):
        end = min(start + chunk_size, len(msg))
        # try to split on a newline boundary for readability
        nl = msg.rfind("\n", start, end)
        if nl > start + 200:
            end = nl
        _post_to_discord(webhook_url, msg[start:end].strip())
        start = end


@product_bp.route('/product_resource_placeholder', methods=['POST'])
def product_placeholder():
    """
    This is a placeholder for a future Product Management AI Resource.
    """
    return jsonify({
        "status": "placeholder",
        "message": "This endpoint is reserved for a future Product AI Resource.",
        "details": "Potential integrations: Jira, Asana, Figma, etc."
    })

@product_bp.route('/create_release_notes', methods=['POST'])
def create_release_notes():
    """
    Create release notes by querying Jira issues for a Fix Version.

    Request JSON:
      { "fix_version": "1.1.0", "jql_extra": "AND statusCategory = Done" }  (jql_extra optional)
    """
    data = request.json or {}
    is_valid, error_msg = validate_request_data(data, required_fields=["fix_version"])
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    fix_version = data.get("fix_version")
    jql_extra = (data.get("jql_extra") or "").strip()
    try:
        service = CreateReleaseNotesService()
        result = service.create(fix_version=fix_version, jql_extra=jql_extra)

        # Optional: post the release notes to the product Discord channel
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL_PRODUCT")
        if webhook_url:
            title = result.get("release_title") or f"Release {result.get('release_version', fix_version)}"
            sections = result.get("sections") or {}
            new_features = sections.get("features") or []
            improvements = sections.get("improvements") or []
            bug_fixes = sections.get("bug_fixes") or []

            lines = [f"# 🚀 {title}", ""]
            lines.append("**New features:**")
            lines.extend([f"- {x}" for x in new_features] or ["- (None)"])
            lines.append("")
            lines.append("**Improvements:**")
            lines.extend([f"- {x}" for x in improvements] or ["- (None)"])
            lines.append("")
            lines.append("**Bug Fixes:**")
            lines.extend([f"- {x}" for x in bug_fixes] or ["- (None)"])

            _post_to_discord_in_chunks(webhook_url, "\n".join(lines))

            # Add comms pack: social drafts + blog draft (as additional messages)
            social = result.get("social") or {}
            social_msg = "\n".join(
                [
                    "## 📣 Social drafts",
                    "",
                    f"**X:** {social.get('x','')}".strip(),
                    "",
                    f"**LinkedIn:** {social.get('linkedin','')}".strip(),
                    "",
                    f"**Facebook:** {social.get('facebook','')}".strip(),
                    "",
                    f"**Instagram:** {social.get('instagram','')}".strip(),
                ]
            ).strip()
            _post_to_discord_in_chunks(webhook_url, social_msg)

            blog = result.get("blog_markdown") or ""
            if blog:
                _post_to_discord_in_chunks(webhook_url, "## 📝 Proposed blog post\n\n" + blog)

        return jsonify(result)
    except ReleaseNotesError as e:
        # If it's our validation, treat as 400; otherwise 500.
        msg = str(e)
        status = 400 if any(
            s in msg.lower()
            for s in ["fix_version", "invalid", "missing required", "required"]
        ) else 500
        return jsonify({"error": sanitize_error_message(msg)}), status
    except Exception as e:
        logger.error("Error in create_release_notes", exc_info=True)
        return jsonify({"error": sanitize_error_message(str(e))}), 500


@product_bp.route('/progress_updates', methods=['POST'])
def progress_updates():
    """
    Generate a team progress update from Jira issues moved to Done in the last N days.
    Request JSON (optional): { "days": 7, "post_to_discord": true, "jql_extra": "..." }
    Default jql_extra is "AND statusCategory = Done". Specify the period with `days` (default 7).
    """
    data = request.json or {}
    days = int(data.get("days", 7))
    if days < 1 or days > 365:
        return jsonify({"error": "days must be between 1 and 365"}), 400
    post_to_discord = bool(data.get("post_to_discord", True))
    # Default jql_extra for progress report: narrow to statusCategory = Done (can override via request).
    jql_extra = (data.get("jql_extra") or "AND statusCategory = Done").strip()

    try:
        service = ProgressUpdatesService()
        result = service.run(days=days, jql_extra=jql_extra)

        message = result.get("message", "")
        if post_to_discord and message:
            webhook_url = os.environ.get("DISCORD_WEBHOOK_URL_PRODUCT")
            if webhook_url:
                _post_to_discord_in_chunks(webhook_url, message)
                result["posted_to_discord"] = True
            else:
                result["posted_to_discord"] = False
        else:
            result["posted_to_discord"] = False

        return jsonify(result)
    except ProgressUpdatesError as e:
        logger.warning("Progress updates error: %s", e)
        return jsonify({"error": sanitize_error_message(str(e))}), 500
    except Exception as e:
        logger.error("Error in progress_updates", exc_info=True)
        return jsonify({"error": sanitize_error_message(str(e))}), 500


def get_manifest():
    """Returns the manifest for the product tools."""
    return {
        "name": "Product Tools",
        "description": "Tools for product management.",
        "tools": [
            {
                "name": "product_resource_placeholder",
                "description": "Placeholder for a future Product Management AI Resource.",
                "path": "/mcp/tools/product_resource_placeholder",
                "method": "POST"
            },
            {
                "name": "create_release_notes",
                "description": "Query Jira by Fix Version and generate multi-channel release notes.",
                "path": "/mcp/tools/create_release_notes",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fix_version": {"type": "string", "description": "Jira Fix Version, e.g. 1.1.0"},
                        "jql_extra": {"type": "string", "description": "Optional JQL fragment to narrow results (e.g. AND statusCategory = Done)"}
                    },
                    "required": ["fix_version"]
                }
            },
            {
                "name": "progress_updates",
                "description": "Generate a team progress update from Jira issues moved to Done in the last N days (AI coach message, optional Discord post). Specify the period with days (default 7). Uses jql_extra default AND statusCategory = Done.",
                "path": "/mcp/tools/progress_updates",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days": {"type": "integer", "description": "Number of days to look back (default 7)", "default": 7},
                        "post_to_discord": {"type": "boolean", "description": "Post the message to product Discord webhook", "default": True},
                        "jql_extra": {"type": "string", "description": "JQL fragment to narrow results; default AND statusCategory = Done", "default": "AND statusCategory = Done"}
                    }
                }
            }
        ]
    }
