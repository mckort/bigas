"""REST API for internal Kanban boards and tickets."""
from __future__ import annotations

import logging

from flask import Blueprint, g, jsonify, request

from bigas.chat.auth import require_chat_auth
from bigas.tickets.config import jira_configured
from bigas.tickets.constants import columns_for_board
from bigas.tickets.attachments import AttachmentError
from bigas.tickets.service import (
    TicketService,
    comment_author_name,
    run_ticket_status_automation,
    ticket_to_api,
)

logger = logging.getLogger(__name__)

tickets_bp = Blueprint("tickets", __name__)


@tickets_bp.route("/api/boards", methods=["GET", "POST"])
@require_chat_auth
def boards():
    user_id = g.chat_user["uid"]
    service = TicketService()
    if request.method == "GET":
        return jsonify(
            {
                "boards": service.list_boards(user_id),
                "jira_import_available": jira_configured(),
            }
        )

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
            labels=body.get("labels"),
            parent_key=(body.get("parent_key") or body.get("parent_epic_key") or "").strip()
            or None,
            thread_id=(body.get("thread_id") or "").strip() or None,
        )
        return jsonify({"ticket": ticket}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@tickets_bp.route("/api/boards/<board_id>/sync-jira", methods=["POST"])
@require_chat_auth
def board_sync_jira(board_id: str):
    from bigas.tickets.jira_import import JiraImportError, dispatch_jira_board_sync
    from bigas.tickets.store import get_ticket_store

    user_id = g.chat_user["uid"]
    store = get_ticket_store()
    board = store.get_board(board_id)
    if not board or board.get("user_id") != user_id:
        return jsonify({"error": "Board not found"}), 404
    if not store.try_begin_jira_sync(board_id, user_id=user_id):
        return jsonify({"ok": False, "status": "running", "error": "Jira sync already in progress"}), 409

    try:
        result = dispatch_jira_board_sync(user_id=user_id, board_id=board_id)
    except JiraImportError as exc:
        store.finish_jira_sync(board_id, user_id=user_id, status="failed", error=str(exc))
        return jsonify({"error": str(exc)}), 400

    if result.get("status") != "started":
        store.finish_jira_sync(board_id, user_id=user_id, status="completed", result=result)
    status_code = 202 if result.get("status") == "started" else 200
    return jsonify(result), status_code


@tickets_bp.route("/api/boards/<board_id>/jira-sync-status", methods=["GET"])
@require_chat_auth
def board_jira_sync_status(board_id: str):
    from bigas.tickets.store import get_ticket_store

    user_id = g.chat_user["uid"]
    store = get_ticket_store()
    board = store.get_board(board_id)
    if not board or board.get("user_id") != user_id:
        return jsonify({"error": "Board not found"}), 404
    sync = store.get_jira_sync(board_id) or {"status": "idle"}
    return jsonify({"jira_sync": sync})


@tickets_bp.route("/api/boards/<board_id>/sync-jira-worker", methods=["POST"])
@require_chat_auth
def board_sync_jira_worker(board_id: str):
    """Loopback worker for long-running Jira imports."""
    from bigas.tickets.jira_import import JiraImportError, sync_jira_board

    user_id = g.chat_user["uid"]
    from bigas.tickets.store import get_ticket_store

    store = get_ticket_store()
    board = store.get_board(board_id)
    if not board or board.get("user_id") != user_id:
        return jsonify({"error": "Board not found"}), 404

    try:
        result = sync_jira_board(user_id=user_id, board_id=board_id)
    except JiraImportError as exc:
        store.finish_jira_sync(board_id, user_id=user_id, status="failed", error=str(exc))
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        store.finish_jira_sync(board_id, user_id=user_id, status="failed", error=str(exc))
        logger.exception("Jira sync worker failed for board %s", board_id)
        raise

    store.finish_jira_sync(board_id, user_id=user_id, status="completed", result=result)
    return jsonify(result)


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
@require_chat_auth
def ticket_automation_worker():
    """
    Loopback worker for ticket status automation after an authenticated drag.

    Dispatched to 127.0.0.1 so Cloud Run allocates CPU for AI handlers.
    Requires the same chat login as the board — not a public webhook.
    """
    user_id = g.chat_user["uid"]
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
    board = store.get_board(ticket.get("board_id") or "")
    if not board or board.get("user_id") != user_id:
        return jsonify({"error": "Forbidden"}), 403

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
        "issue_type",
        "assignee",
        "fix_version",
        "thread_id",
        "marketing",
        "labels",
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


@tickets_bp.route("/api/tickets/<ticket_id>/attachments", methods=["POST"])
@require_chat_auth
def ticket_attachments(ticket_id: str):
    user_id = g.chat_user["uid"]
    from bigas.tickets.store import get_ticket_store

    store = get_ticket_store()
    ticket = store.get_ticket(ticket_id)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404
    board = store.get_board(ticket.get("board_id") or "")
    if board and board.get("user_id") != user_id:
        return jsonify({"error": "Forbidden"}), 403

    uploaded = request.files.get("file")
    if uploaded is None or not (uploaded.filename or "").strip():
        return jsonify({"error": "file is required"}), 400
    data = uploaded.read()
    try:
        attachment = TicketService().add_attachment(
            ticket_id,
            filename=uploaded.filename or "attachment",
            content_type=uploaded.mimetype,
            data=data,
            uploaded_by=user_id,
        )
    except AttachmentError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"attachment": attachment}), 201


@tickets_bp.route(
    "/api/tickets/<ticket_id>/attachments/<attachment_id>",
    methods=["GET", "DELETE"],
)
@require_chat_auth
def ticket_attachment_detail(ticket_id: str, attachment_id: str):
    user_id = g.chat_user["uid"]
    from io import BytesIO

    from flask import send_file

    from bigas.tickets.store import get_ticket_store

    store = get_ticket_store()
    ticket = store.get_ticket(ticket_id)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404
    board = store.get_board(ticket.get("board_id") or "")
    if board and board.get("user_id") != user_id:
        return jsonify({"error": "Forbidden"}), 403

    service = TicketService()
    if request.method == "DELETE":
        removed = service.delete_attachment(ticket_id, attachment_id)
        if not removed:
            return jsonify({"error": "Attachment not found"}), 404
        return jsonify({"ok": True, "attachment": removed})

    loaded = service.get_attachment_bytes(ticket_id, attachment_id)
    if not loaded:
        return jsonify({"error": "Attachment not found"}), 404
    record, data = loaded
    return send_file(
        BytesIO(data),
        mimetype=record.get("content_type") or "application/octet-stream",
        as_attachment=False,
        download_name=record.get("filename") or "attachment",
    )


@tickets_bp.route("/api/tickets/<ticket_id>/comments", methods=["POST"])
@require_chat_auth
def ticket_comments(ticket_id: str):
    user_id = g.chat_user["uid"]
    body = request.get_json(silent=True) or {}
    text = (body.get("body") or "").strip()
    if not text:
        return jsonify({"error": "body is required"}), 400

    from bigas.tickets.store import get_ticket_store

    store = get_ticket_store()
    ticket = store.get_ticket(ticket_id)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404
    board = store.get_board(ticket.get("board_id") or "")
    if board and board.get("user_id") != user_id:
        return jsonify({"error": "Forbidden"}), 403

    comment = TicketService().add_comment(
        ticket_id,
        body=text,
        author_name=comment_author_name(g.chat_user),
        author_id=user_id,
    )
    if not comment:
        return jsonify({"error": "Could not add comment"}), 400
    return jsonify({"comment": comment}), 201


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
