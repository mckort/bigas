"""Flask blueprint for the Bigas chat web interface API."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Blueprint, g, jsonify, request, send_from_directory

from bigas.access import require_bigas_access_key
from bigas.agents.chief_of_staff import handle_chat_message, post_agent_callback
from bigas.chat.auth import is_chat_admin, require_chat_auth, verify_callback_secret
from bigas.chat.db import (
    DEFAULT_ACTIVITY_KEEP_DAYS,
    DEFAULT_ACTIVITY_MAX_DELETE,
    get_chat_store,
)
from bigas.resources.devops.pipeline import poll_deploy_postcheck

logger = logging.getLogger(__name__)

chat_bp = Blueprint("chat", __name__)

FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
FRONTEND_PUBLIC = Path(__file__).resolve().parents[3] / "frontend" / "public"
BRAND_ICON_FILES = {
    "favicon.ico",
    "favicon.png",
    "favicon-16x16.png",
    "favicon-32x32.png",
    "favicon-48x48.png",
    "apple-touch-icon.png",
    "bigas-logo.png",
}


def chat_enabled() -> bool:
    return os.environ.get("CHAT_ENABLED", "true").strip().lower() in ("1", "true", "yes")


@chat_bp.route("/api/auth/verify", methods=["POST"])
@require_chat_auth
def verify_auth():
    store = get_chat_store()
    user = g.chat_user
    profile = store.upsert_user(user["uid"], user.get("email") or "")
    return jsonify({"user": profile, "ok": True})


@chat_bp.route("/api/auth/config", methods=["GET"])
def auth_config():
    """Public config for the frontend Firebase SDK."""
    return jsonify(
        {
            "auth_mode": os.environ.get("CHAT_AUTH_MODE") or "dev",
            "firebase": {
                "apiKey": os.environ.get("VITE_FIREBASE_API_KEY") or os.environ.get("FIREBASE_WEB_API_KEY"),
                "authDomain": os.environ.get("VITE_FIREBASE_AUTH_DOMAIN"),
                "projectId": os.environ.get("VITE_FIREBASE_PROJECT_ID") or os.environ.get("FIREBASE_PROJECT_ID"),
            },
        }
    )


@chat_bp.route("/api/agents", methods=["GET"])
@require_chat_auth
def list_agents():
    store = get_chat_store()
    return jsonify({"agents": store.list_agents()})


@chat_bp.route("/api/agents/<agent_id>", methods=["PUT"])
@require_chat_auth
def update_agent(agent_id: str):
    if not is_chat_admin(g.chat_user):
        return jsonify({"error": "Admin access required to update agent configuration"}), 403
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    goals = (body.get("system_prompt_goals") or "").strip()
    if not name or not goals:
        return jsonify({"error": "name and system_prompt_goals are required"}), 400
    store = get_chat_store()
    agent = store.update_agent(agent_id, name=name, system_prompt_goals=goals)
    return jsonify({"agent": agent})


@chat_bp.route("/api/chat/threads", methods=["GET", "POST"])
@require_chat_auth
def threads():
    store = get_chat_store()
    user_id = g.chat_user["uid"]
    if request.method == "GET":
        return jsonify({"threads": store.list_threads(user_id)})

    body = request.get_json(silent=True) or {}
    agent_id = (body.get("agent_id") or "chief").strip()
    thread = store.create_thread(user_id, agent_id)
    return jsonify({"thread": thread}), 201


@chat_bp.route("/api/chat/threads/<thread_id>/messages", methods=["GET", "POST"])
@require_chat_auth
def thread_messages(thread_id: str):
    store = get_chat_store()
    user_id = g.chat_user["uid"]
    thread = store.get_thread(thread_id)
    if not thread or thread.get("user_id") != user_id:
        return jsonify({"error": "Thread not found"}), 404

    if request.method == "GET":
        since = request.args.get("since")
        messages = store.list_messages(thread_id, since=since)
        thread_data = store.get_thread(thread_id) or {}
        deploy_poll_active = bool(thread_data.get("pending_deploy_poll"))
        return jsonify({"messages": messages, "deploy_poll_active": deploy_poll_active})

    body = request.get_json(silent=True) or {}
    content = (body.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content is required"}), 400
    client_id = (body.get("client_id") or "").strip() or None

    history_msgs = store.list_messages(thread_id)
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in history_msgs
        if m.get("role") in ("user", "assistant")
    ]

    try:
        result = handle_chat_message(
            thread_id=thread_id,
            user_id=user_id,
            user_message=content,
            history=history,
            client_id=client_id,
        )
        return jsonify(result)
    except Exception as e:
        logger.exception("Chat message handling failed")
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/api/chat/threads/<thread_id>/deploy-poll", methods=["POST"])
@require_chat_auth
def deploy_poll(thread_id: str):
    """Client-driven poll step for DevOps deploy post-check (GitHub Actions + health)."""
    store = get_chat_store()
    user_id = g.chat_user["uid"]
    thread = store.get_thread(thread_id)
    if not thread or thread.get("user_id") != user_id:
        return jsonify({"error": "Thread not found"}), 404
    try:
        result = poll_deploy_postcheck(thread_id)
        messages = store.list_messages(thread_id)
        return jsonify({**result, "messages": messages})
    except Exception as e:
        logger.exception("Deploy post-check poll failed")
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/api/chat/callback", methods=["POST"])
def chat_callback():
    """Sub-agents report async task completion here."""
    if not verify_callback_secret():
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    thread_id = (body.get("thread_id") or "").strip()
    content = (body.get("content") or "").strip()
    agent_id = (body.get("agent_id") or "system").strip()
    if not thread_id or not content:
        return jsonify({"error": "thread_id and content are required"}), 400
    message = post_agent_callback(thread_id, content, agent_id=agent_id)
    return jsonify({"message": message})


@chat_bp.route("/api/feed", methods=["GET"])
@require_chat_auth
def activity_feed():
    store = get_chat_store()
    since = request.args.get("since")
    limit = min(int(request.args.get("limit", 50)), 100)
    events = store.list_activity(since=since, limit=limit)
    return jsonify({"events": events})


def _int_param(data: dict, name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = data.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


@chat_bp.route("/mcp/tools/cleanup_old_activity", methods=["POST"])
@require_bigas_access_key
def cleanup_old_activity():
    """Delete chat activity-feed events older than keep_days (default 7)."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    keep_days = _int_param(data, "keep_days", DEFAULT_ACTIVITY_KEEP_DAYS, minimum=1, maximum=365)
    max_to_delete = _int_param(
        data, "max_to_delete", DEFAULT_ACTIVITY_MAX_DELETE, minimum=1, maximum=500
    )
    try:
        store = get_chat_store()
        total_deleted = 0
        while True:
            deleted = store.delete_old_activity(
                keep_days=keep_days, max_to_delete=max_to_delete
            )
            total_deleted += deleted
            if deleted < max_to_delete:
                break
        return jsonify(
            {
                "status": "success",
                "deleted": total_deleted,
                "keep_days": keep_days,
                "max_to_delete": max_to_delete,
                "message": (
                    f"Deleted {total_deleted} activity events older than {keep_days} days"
                ),
            }
        )
    except Exception:
        logger.exception("Failed to clean up old chat activity")
        return jsonify({"error": "Failed to clean up old chat activity"}), 500


def get_manifest():
    """Return chat housekeeping tools for the combined MCP manifest."""
    return {
        "name": "Chat Tools",
        "description": "Housekeeping for the Bigas chat activity feed.",
        "tools": [
            {
                "name": "cleanup_old_activity",
                "description": (
                    "Delete chat activity-feed events older than keep_days (default 7). "
                    "Intended for a weekly Cloud Scheduler job."
                ),
                "path": "/mcp/tools/cleanup_old_activity",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keep_days": {
                            "type": "integer",
                            "description": "Keep events from the last N days.",
                            "default": DEFAULT_ACTIVITY_KEEP_DAYS,
                            "minimum": 1,
                            "maximum": 365,
                        },
                        "max_to_delete": {
                            "type": "integer",
                            "description": "Max events to delete per batch (Firestore batch limit 500); loops until none remain.",
                            "default": DEFAULT_ACTIVITY_MAX_DELETE,
                            "minimum": 1,
                            "maximum": 500,
                        },
                    },
                },
            }
        ],
    }


@chat_bp.route("/")
def serve_frontend_root():
    """Serve the React SPA entrypoint when built; fall back to API-only message."""
    if not FRONTEND_DIST.is_dir():
        return jsonify(
            {
                "service": "bigas-chat",
                "message": "Chat API is available at /api/*. Build frontend with `cd frontend && npm run build`.",
            }
        )
    return send_from_directory(FRONTEND_DIST, "index.html")


def _send_brand_icon(filename: str):
    for folder in (FRONTEND_DIST, FRONTEND_PUBLIC):
        path = folder / filename
        if path.is_file():
            return send_from_directory(folder, filename)
    return jsonify({"error": "Not found"}), 404


@chat_bp.route("/favicon.ico")
@chat_bp.route("/favicon.png")
@chat_bp.route("/favicon-16x16.png")
@chat_bp.route("/favicon-32x32.png")
@chat_bp.route("/favicon-48x48.png")
@chat_bp.route("/apple-touch-icon.png")
@chat_bp.route("/bigas-logo.png")
def serve_brand_icon():
    """Serve tab/app icons and the chat logo from the Vite build, falling back to frontend/public."""
    name = (request.path or "").lstrip("/")
    if name not in BRAND_ICON_FILES:
        return jsonify({"error": "Not found"}), 404
    return _send_brand_icon(name)


@chat_bp.route("/assets/<path:path>")
def serve_frontend_assets(path: str):
    """Serve built frontend static assets."""
    if not FRONTEND_DIST.is_dir():
        return jsonify({"error": "Not found"}), 404
    asset_path = FRONTEND_DIST / "assets" / path
    if not asset_path.is_file():
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(FRONTEND_DIST / "assets", path)


@chat_bp.route("/<path:path>")
def serve_frontend_static(path: str):
    """Serve other built frontend files; never swallow API or MCP routes."""
    if path.startswith(("api/", "mcp/", ".well-known/")):
        return jsonify({"error": "Not found"}), 404
    if not FRONTEND_DIST.is_dir():
        return jsonify({"error": "Not found"}), 404
    file_path = FRONTEND_DIST / path
    if file_path.is_file():
        return send_from_directory(FRONTEND_DIST, path)
    # Fallback for SPA client-side routing (e.g. /thread/123)
    return send_from_directory(FRONTEND_DIST, "index.html")
