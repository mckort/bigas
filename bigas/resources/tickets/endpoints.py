"""REST API for internal Kanban boards and tickets."""
from __future__ import annotations

import logging
import os

from flask import Blueprint, g, jsonify, request

from bigas.chat.auth import require_chat_auth
from bigas.tickets.constants import columns_for_board
from bigas.tickets.service import TicketService, run_ticket_status_automation

logger = logging.getLogger(__name__)

tickets_bp = Blueprint("tickets", __name__)


@tickets_bp.route("/api/boards", methods=["GET", "POST"])
@require_chat_auth
def boards():
    user_id = g.chat_user["uid"]
    service = TicketService()
    if request.method == "GET":
        return jsonify({"boards": service.list_boards(user_id)})

    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    project_key = (body.get("project_key") or "").strip().upper() or None
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        board = service.create_board(user_id, name=name, project_key=project_key)
        return jsonify({"board": board}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@tickets_bp.route("/api/boards/<board_id>", methods=["DELETE", "PUT"])
@require_chat_auth
def board_detail(board_id: str):
    user_id = g.chat_user["uid"]
    service = TicketService()
    if request.method == "DELETE":
        if service.delete_board(board_id, user_id=user_id):
            return jsonify({"ok": True})
        return jsonify({"error": "Board not found"}), 404

    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip() or None
    from bigas.tickets.store import get_ticket_store

    store = get_ticket_store()
    board = store.update_board(board_id, user_id=user_id, name=name)
    if not board:
        return jsonify({"error": "Board not found"}), 404
    return jsonify(
        {
            "board": {
                **board,
                "columns": columns_for_board(project_key=board.get("project_key")),
                "workflow_enabled": bool(board.get("project_key")),
            }
        }
    )


@tickets_bp.route("/api/boards/<board_id>/tickets", methods=["GET", "POST"])
@require_chat_auth
def board_tickets(board_id: str):
    user_id = g.chat_user["uid"]
    service = TicketService()
    if request.method == "GET":
        tickets = service.list_tickets(board_id, user_id=user_id)
        return jsonify({"tickets": tickets})

    body = request.get_json(silent=True) or {}
    title = (body.get("title") or body.get("summary") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    try:
        ticket = service.create_ticket(
            board_id,
            user_id=user_id,
            title=title,
            description=(body.get("description") or "").strip(),
            status=(body.get("status") or "To Do").strip(),
            issue_type=(body.get("issue_type") or "Task").strip(),
            assignee=(body.get("assignee") or "").strip() or None,
            fix_version=(body.get("fix_version") or "").strip() or None,
            marketing=bool(body.get("marketing")),
            parent_key=(body.get("parent_key") or body.get("parent_epic_key") or "").strip()
            or None,
            thread_id=(body.get("thread_id") or "").strip() or None,
        )
        return jsonify({"ticket": ticket}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@tickets_bp.route("/api/tickets/by-key/<key>", methods=["GET"])
@require_chat_auth
def ticket_by_key(key: str):
    user_id = g.chat_user["uid"]
    service = TicketService()
    ticket = service.lookup_ticket(key)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404
    from bigas.tickets.store import get_ticket_store

    board = get_ticket_store().get_board(ticket.get("board_id") or "")
    if board and board.get("user_id") != user_id:
        return jsonify({"error": "Forbidden"}), 403
    return jsonify({"ticket": ticket})


@tickets_bp.route("/api/tickets/automation-worker", methods=["POST"])
def ticket_automation_worker():
    """
    Internal worker endpoint for ticket status automation.

    Dispatched via HTTP from TicketService so Cloud Run allocates CPU for AI
    handlers instead of relying on background threads after the response is sent.
    """
    from bigas.resources.devops.self_healing import webhook_secret
    from bigas.resources.product.jira_automation.service import (
        extract_webhook_secret_from_headers,
        verify_webhook_secret,
    )

    header_secret = extract_webhook_secret_from_headers(request.headers)
    secret = webhook_secret()
    if secret and verify_webhook_secret(header_secret, secret):
        pass
    else:
        keys = [k.strip() for k in (os.environ.get("BIGAS_ACCESS_KEYS") or "").split(",") if k.strip()]
        provided = (request.headers.get("X-Bigas-Access-Key") or "").strip()
        if not keys or provided not in keys:
            return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    ticket_id = (data.get("ticket_id") or "").strip()
    issue_key = (data.get("issue_key") or "").strip()
    new_status = (data.get("new_status") or "").strip()
    old_status = (data.get("old_status") or "").strip()
    project_key = (data.get("project_key") or "").strip()
    if not ticket_id or not new_status:
        return jsonify({"error": "ticket_id and new_status are required"}), 400

    from bigas.tickets.store import get_ticket_store

    store = get_ticket_store()
    ticket = store.get_ticket(ticket_id)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404

    run_ticket_status_automation(
        ticket,
        old_status=old_status,
        new_status=new_status,
        project_key=project_key or (issue_key.split("-", 1)[0] if "-" in issue_key else ""),
    )
    return jsonify({"ok": True, "issue_key": issue_key or ticket.get("key")})


@tickets_bp.route("/api/tickets/<ticket_id>", methods=["GET", "PUT", "DELETE"])
@require_chat_auth
def ticket_detail(ticket_id: str):
    user_id = g.chat_user["uid"]
    service = TicketService()
    from bigas.tickets.store import get_ticket_store

    store = get_ticket_store()

    if request.method == "GET":
        ticket = store.get_ticket(ticket_id)
        if not ticket:
            return jsonify({"error": "Ticket not found"}), 404
        board = store.get_board(ticket.get("board_id") or "")
        if board and board.get("user_id") != user_id:
            return jsonify({"error": "Forbidden"}), 403
        from bigas.tickets.service import ticket_to_api

        return jsonify({"ticket": ticket_to_api(ticket)})

    if request.method == "DELETE":
        if store.delete_ticket(ticket_id, user_id=user_id):
            return jsonify({"ok": True})
        return jsonify({"error": "Ticket not found"}), 404

    body = request.get_json(silent=True) or {}
    existing = store.get_ticket(ticket_id)
    if not existing:
        return jsonify({"error": "Ticket not found"}), 404
    fields = {}
    for key in (
        "title",
        "description",
        "status",
        "assignee",
        "fix_version",
        "thread_id",
        "marketing",
        "parent_key",
    ):
        if key in body:
            fields[key] = body[key]
    ticket = service.update_ticket(
        ticket_id,
        user_id=user_id,
        previous_status=existing.get("status"),
        **fields,
    )
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404
    return jsonify({"ticket": ticket})


@tickets_bp.route("/api/tickets/<ticket_id>/transition", methods=["POST"])
@require_chat_auth
def ticket_transition(ticket_id: str):
    """Move ticket to the next column."""
    user_id = g.chat_user["uid"]
    from bigas.tickets.store import get_ticket_store

    store = get_ticket_store()
    ticket = store.get_ticket(ticket_id)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404
    board = store.get_board(ticket.get("board_id") or "")
    if board and board.get("user_id") != user_id:
        return jsonify({"error": "Forbidden"}), 403
    try:
        result = TicketService().transition_to_next(ticket.get("key") or "")
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc), "success": False}), 400
