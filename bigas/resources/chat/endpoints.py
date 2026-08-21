"""Flask blueprint for the Bigas chat web interface API."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Blueprint, g, jsonify, request, send_from_directory

from bigas.access import require_bigas_access_key
from bigas.agents.chief_of_staff import handle_chat_message, post_agent_callback
from bigas.agents.proactive_engine import ProactiveGoalEngineError, run_evaluation_loop
from bigas.agents.task_runtime import tick_all_open_tasks, tick_thread
from bigas.chat.auth import is_chat_admin, require_chat_auth, verify_callback_secret
from bigas.chat.db import (
    DEFAULT_ACTIVITY_KEEP_DAYS,
    DEFAULT_ACTIVITY_MAX_DELETE,
    get_chat_store,
)
from bigas.chat.tasks import thread_has_open_tasks

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
        task_poll_active = thread_has_open_tasks(thread_id)
        thread_data = store.get_thread(thread_id) or {}
        deploy_poll_active = task_poll_active or bool(thread_data.get("pending_deploy_poll"))
        return jsonify({
            "messages": messages,
            "deploy_poll_active": deploy_poll_active,
            "task_poll_active": task_poll_active,
        })

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


@chat_bp.route("/api/chat/threads/<thread_id>/tasks/tick", methods=["POST"])
@chat_bp.route("/api/chat/threads/<thread_id>/deploy-poll", methods=["POST"])
@require_chat_auth
def thread_task_tick(thread_id: str):
    """Advance open agent tasks for this thread (deploy poll, nudges, completion)."""
    store = get_chat_store()
    user_id = g.chat_user["uid"]
    thread = store.get_thread(thread_id)
    if not thread or thread.get("user_id") != user_id:
        return jsonify({"error": "Thread not found"}), 404
    try:
        result = tick_thread(thread_id)
        messages = store.list_messages(thread_id)
        return jsonify({**result, "messages": messages, "task_poll_active": result.get("active")})
    except Exception as e:
        logger.exception("Task tick failed")
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/api/agents/tick-tasks", methods=["POST"])
def tick_tasks():
    """Scheduler webhook: nudge overdue tasks and advance deploy polls (Cloud Run safe)."""
    try:
        return jsonify(tick_all_open_tasks())
    except Exception:
        logger.exception("tick-tasks failed")
        return jsonify({"ok": False, "error": "Internal error"}), 500


@chat_bp.route("/api/chat/callback", methods=["POST"])
def chat_callback():
    """Sub-agents report async task completion here."""
    if not verify_callback_secret():
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    thread_id = (body.get("thread_id") or "").strip()
    content = (body.get("content") or "").strip()
    agent_id = (body.get("agent_id") or "system").strip()
    task_id = (body.get("task_id") or "").strip() or None
    final = bool(body.get("final") or body.get("complete"))
    if (not thread_id and not task_id) or not content:
        return jsonify({"error": "content and thread_id or task_id are required"}), 400
    if not thread_id and task_id:
        from bigas.chat.tasks import get_task

        task = get_task(task_id) or {}
        thread_id = task.get("source_thread_id") or ""
        if not thread_id:
            return jsonify({"error": "thread_id is required when task has no source thread"}), 400
    message = post_agent_callback(
        thread_id, content, agent_id=agent_id, task_id=task_id, final=final
    )
    return jsonify({"message": message})


@chat_bp.route("/api/jira/transition", methods=["POST"])
@require_chat_auth
def jira_transition():
    """Move a Jira issue to the next workflow column (chat UI button action)."""
    from bigas.resources.product.jira_transition.service import (
        JiraTransitionError,
        transition_issue_to_next_column,
    )

    body = request.get_json(silent=True) or {}
    issue_key = (body.get("issue_key") or "").strip()
    if not issue_key:
        return jsonify({"error": "issue_key is required"}), 400
    try:
        result = transition_issue_to_next_column(issue_key)
        return jsonify(result)
    except JiraTransitionError as exc:
        return jsonify({"error": str(exc), "success": False}), 400
    except Exception:
        logger.exception("Jira transition failed for %s", issue_key)
        return jsonify({"error": "Failed to transition Jira issue", "success": False}), 500


@chat_bp.route("/api/feed", methods=["GET"])
@require_chat_auth
def activity_feed():
    store = get_chat_store()
    since = request.args.get("since")
    limit = min(int(request.args.get("limit", 50)), 100)
    events = store.list_activity(since=since, limit=limit)
    return jsonify({"events": events})


@chat_bp.route("/api/agents/evaluate-goals", methods=["POST"])
def evaluate_goals():
    """
    Proactive Goal Engine — scheduled Epic evaluation (Cloud Scheduler).

    Auth: always required — X-Bigas-Access-Key or Authorization: Bearer (BIGAS_ACCESS_KEY),
    or legacy CRON_SECRET via Bearer / X-Cron-Secret. Enforced in app middleware.

    Body JSON:
      { "timeframe_days": 7 }  — lookback for progress; tasks target the next N days.
    """
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    timeframe_days = _int_param(data, "timeframe_days", 7, minimum=1, maximum=365)

    try:
        job_result = run_evaluation_loop(timeframe_days=timeframe_days)
    except ProactiveGoalEngineError as e:
        logger.error("evaluate-goals failed: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500
    except Exception:
        logger.exception("evaluate-goals unexpected failure")
        return jsonify({"ok": False, "error": "Internal error"}), 500

    status = 200 if job_result.get("ok") else 500
    return jsonify(job_result), status


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
