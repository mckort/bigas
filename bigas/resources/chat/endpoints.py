"""Flask blueprint for the Bigas chat web interface API."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Blueprint, g, jsonify, request, send_from_directory

from bigas.agents.chief_of_staff import handle_chat_message, post_agent_callback
from bigas.chat.auth import is_chat_admin, require_chat_auth, verify_callback_secret
from bigas.chat.db import get_chat_store

logger = logging.getLogger(__name__)

chat_bp = Blueprint("chat", __name__)

FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


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
        return jsonify({"messages": messages})

    body = request.get_json(silent=True) or {}
    content = (body.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content is required"}), 400

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
        )
        return jsonify(result)
    except Exception as e:
        logger.exception("Chat message handling failed")
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
    return jsonify({"error": "Not found"}), 404
