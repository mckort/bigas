"""REST and MCP endpoints for optional board outbound email."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from flask import Blueprint, g, jsonify, request

from bigas.chat.auth import require_chat_auth
from bigas.resources.email.outbound_service import (
    campaign_summary,
    generate_email_draft,
    preview_message,
    resolve_recipients_for_send,
    start_campaign_send,
)
from bigas.resources.email.outbound_store import (
    assert_board_owner,
    config_for_provider,
    get_outbound_email_store,
    outbound_email_enabled,
    parse_recipient_csv,
    public_email_settings,
)

logger = logging.getLogger(__name__)

outbound_email_bp = Blueprint("outbound_email", __name__)


def _feature_guard():
    if not outbound_email_enabled():
        return jsonify({"error": "Outbound email is disabled (set ENABLE_OUTBOUND_EMAIL=true)"}), 404
    return None


def get_manifest() -> Dict[str, Any]:
    if not outbound_email_enabled():
        return {"tools": []}
    return {
        "name": "Outbound Email",
        "description": "Optional marketing outreach email tools (per-board SMTP).",
        "tools": [
            {
                "name": "draft_marketing_email",
                "description": (
                    "Generate a plain-text outreach email with {{first_name}} placeholders. "
                    "Requires board_id. Saves draft on the board for human review before sending."
                ),
                "path": "/mcp/tools/draft_marketing_email",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "board_id": {"type": "string"},
                        "prompt": {"type": "string"},
                        "tone": {"type": "string", "default": "professional"},
                        "goal": {"type": "string"},
                    },
                    "required": ["board_id", "prompt"],
                },
            },
            {
                "name": "preview_marketing_email",
                "description": "Preview subject/body after {{first_name}} substitution for a sample recipient.",
                "path": "/mcp/tools/preview_marketing_email",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "board_id": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                        "first_name": {"type": "string", "default": "Alex"},
                        "email": {"type": "string", "default": "alex@example.com"},
                    },
                    "required": ["board_id", "subject", "body"],
                },
            },
            {
                "name": "list_board_recipients",
                "description": "List uploaded outreach recipients (first_name, email) for a board.",
                "path": "/mcp/tools/list_board_recipients",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {"board_id": {"type": "string"}},
                    "required": ["board_id"],
                },
            },
        ],
    }


@outbound_email_bp.route("/api/outbound-email/enabled", methods=["GET"])
def outbound_enabled_flag():
    return jsonify({"enabled": outbound_email_enabled()})


@outbound_email_bp.route("/api/boards/<board_id>/email-settings", methods=["GET", "PUT"])
@require_chat_auth
def board_email_settings(board_id: str):
    blocked = _feature_guard()
    if blocked:
        return blocked
    user_id = g.chat_user["uid"]
    try:
        assert_board_owner(board_id, user_id)
    except PermissionError:
        return jsonify({"error": "Board not found"}), 404

    store = get_outbound_email_store()
    if request.method == "GET":
        return jsonify({"settings": public_email_settings(board_id)})

    body = request.get_json(silent=True) or {}
    saved = store.save_email_config(board_id, body)
    return jsonify({"settings": public_email_settings(board_id), "updated_at": saved.get("updated_at")})


@outbound_email_bp.route("/api/boards/<board_id>/email-settings/test", methods=["POST"])
@require_chat_auth
def board_email_settings_test(board_id: str):
    blocked = _feature_guard()
    if blocked:
        return blocked
    user_id = g.chat_user["uid"]
    try:
        assert_board_owner(board_id, user_id)
    except PermissionError:
        return jsonify({"error": "Board not found"}), 404

    store = get_outbound_email_store()
    config = store.get_email_config(board_id)
    if not config:
        return jsonify({"ok": False, "error": "Save email settings before testing."}), 400
    try:
        config_for_provider(config).test_connection()
        return jsonify({"ok": True, "message": "SMTP connection and authentication succeeded."})
    except Exception as exc:
        logger.info("SMTP test failed for board %s: %s", board_id, exc)
        return jsonify({"ok": False, "error": str(exc)}), 400


@outbound_email_bp.route("/api/boards/<board_id>/recipients", methods=["GET", "POST"])
@require_chat_auth
def board_recipients(board_id: str):
    blocked = _feature_guard()
    if blocked:
        return blocked
    user_id = g.chat_user["uid"]
    try:
        assert_board_owner(board_id, user_id)
    except PermissionError:
        return jsonify({"error": "Board not found"}), 404

    store = get_outbound_email_store()
    if request.method == "GET":
        return jsonify({"recipients": store.list_recipients(board_id)})

    replace = request.args.get("replace", "false").lower() in ("1", "true", "yes")
    csv_text = ""
    if request.files and "file" in request.files:
        csv_text = request.files["file"].read().decode("utf-8-sig", errors="replace")
    else:
        body = request.get_json(silent=True) or {}
        csv_text = body.get("csv") or ""
    valid, invalid = parse_recipient_csv(csv_text)
    added = store.add_recipients(board_id, valid, replace=replace) if valid else 0
    return jsonify(
        {
            "added": added,
            "valid_count": len(valid),
            "invalid_rows": invalid,
            "recipients": store.list_recipients(board_id),
        }
    )


@outbound_email_bp.route("/api/boards/<board_id>/email-draft", methods=["GET", "PUT"])
@require_chat_auth
def board_email_draft(board_id: str):
    blocked = _feature_guard()
    if blocked:
        return blocked
    user_id = g.chat_user["uid"]
    try:
        assert_board_owner(board_id, user_id)
    except PermissionError:
        return jsonify({"error": "Board not found"}), 404

    store = get_outbound_email_store()
    if request.method == "GET":
        draft = store.get_draft(board_id)
        return jsonify({"draft": draft})

    body = request.get_json(silent=True) or {}
    draft = store.save_draft(
        board_id,
        subject=str(body.get("subject") or ""),
        body=str(body.get("body") or ""),
    )
    return jsonify({"draft": draft})


@outbound_email_bp.route("/api/boards/<board_id>/campaigns/preview", methods=["POST"])
@require_chat_auth
def board_campaign_preview(board_id: str):
    blocked = _feature_guard()
    if blocked:
        return blocked
    user_id = g.chat_user["uid"]
    try:
        assert_board_owner(board_id, user_id)
    except PermissionError:
        return jsonify({"error": "Board not found"}), 404

    body = request.get_json(silent=True) or {}
    subject = str(body.get("subject") or "")
    text = str(body.get("body") or "")
    recipient_id = str(body.get("recipient_id") or "").strip()
    store = get_outbound_email_store()
    sample = None
    if recipient_id:
        for row in store.list_recipients(board_id):
            if row.get("recipient_id") == recipient_id:
                sample = row
                break
    if not sample:
        sample = {
            "first_name": str(body.get("first_name") or "Alex"),
            "email": str(body.get("email") or "alex@example.com"),
        }
    preview = preview_message(subject_template=subject, body_template=text, recipient=sample)
    return jsonify({"preview": preview, "sample_recipient": sample})


@outbound_email_bp.route("/api/boards/<board_id>/campaigns/send", methods=["POST"])
@require_chat_auth
def board_campaign_send(board_id: str):
    blocked = _feature_guard()
    if blocked:
        return blocked
    user_id = g.chat_user["uid"]
    try:
        assert_board_owner(board_id, user_id)
    except PermissionError:
        return jsonify({"error": "Board not found"}), 404

    body = request.get_json(silent=True) or {}
    subject = str(body.get("subject") or "").strip()
    text = str(body.get("body") or "").strip()
    if not subject or not text:
        return jsonify({"error": "subject and body are required"}), 400

    select_all = bool(body.get("select_all"))
    recipient_ids = body.get("recipient_ids") or []
    targets = resolve_recipients_for_send(board_id, recipient_ids, select_all=select_all)
    if not targets:
        return jsonify({"error": "No recipients selected"}), 400

    store = get_outbound_email_store()
    store.save_draft(board_id, subject=subject, body=text)
    campaign = store.create_campaign(
        board_id,
        subject_template=subject,
        body_template=text,
        recipients=targets,
    )
    start_campaign_send(board_id, campaign["campaign_id"])
    return jsonify({"campaign": campaign_summary(campaign)}), 202


@outbound_email_bp.route("/api/boards/<board_id>/campaigns/<campaign_id>", methods=["GET"])
@require_chat_auth
def board_campaign_status(board_id: str, campaign_id: str):
    blocked = _feature_guard()
    if blocked:
        return blocked
    user_id = g.chat_user["uid"]
    try:
        assert_board_owner(board_id, user_id)
    except PermissionError:
        return jsonify({"error": "Board not found"}), 404

    store = get_outbound_email_store()
    campaign = store.get_campaign(campaign_id)
    if not campaign or campaign.get("board_id") != board_id:
        return jsonify({"error": "Campaign not found"}), 404
    return jsonify({"campaign": campaign_summary(campaign)})


def _mcp_board_guard(board_id: str, user_id: str | None) -> None:
    if user_id:
        assert_board_owner(board_id, user_id)


@outbound_email_bp.route("/mcp/tools/draft_marketing_email", methods=["POST"])
def mcp_draft_marketing_email():
    blocked = _feature_guard()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    board_id = str(data.get("board_id") or "").strip()
    prompt = str(data.get("prompt") or "").strip()
    if not board_id or not prompt:
        return jsonify({"error": "board_id and prompt are required"}), 400
    user_id = str(data.get("user_id") or "").strip() or None
    try:
        _mcp_board_guard(board_id, user_id)
    except PermissionError:
        return jsonify({"error": "Board not found"}), 404

    draft = generate_email_draft(
        prompt=prompt,
        tone=str(data.get("tone") or "professional"),
        goal=str(data.get("goal") or ""),
    )
    saved = get_outbound_email_store().save_draft(
        board_id, subject=draft["subject"], body=draft["body"]
    )
    return jsonify(
        {
            "summary": "Draft saved on the board. Review in board email outreach before sending.",
            "draft": saved,
        }
    )


@outbound_email_bp.route("/mcp/tools/preview_marketing_email", methods=["POST"])
def mcp_preview_marketing_email():
    blocked = _feature_guard()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    board_id = str(data.get("board_id") or "").strip()
    subject = str(data.get("subject") or "")
    body = str(data.get("body") or "")
    if not board_id:
        return jsonify({"error": "board_id is required"}), 400
    user_id = str(data.get("user_id") or "").strip() or None
    try:
        _mcp_board_guard(board_id, user_id)
    except PermissionError:
        return jsonify({"error": "Board not found"}), 404

    sample = {
        "first_name": str(data.get("first_name") or "Alex"),
        "email": str(data.get("email") or "alex@example.com"),
    }
    preview = preview_message(subject_template=subject, body_template=body, recipient=sample)
    return jsonify({"preview": preview})


@outbound_email_bp.route("/mcp/tools/list_board_recipients", methods=["POST"])
def mcp_list_board_recipients():
    blocked = _feature_guard()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    board_id = str(data.get("board_id") or "").strip()
    if not board_id:
        return jsonify({"error": "board_id is required"}), 400
    user_id = str(data.get("user_id") or "").strip() or None
    try:
        _mcp_board_guard(board_id, user_id)
    except PermissionError:
        return jsonify({"error": "Board not found"}), 404

    rows = get_outbound_email_store().list_recipients(board_id)
    return jsonify({"recipients": rows, "count": len(rows)})
