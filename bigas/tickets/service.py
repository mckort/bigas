"""Business logic for internal ticket boards."""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

from bigas.tickets.constants import AI_TRIGGER_STATUSES, columns_for_board, next_column
from bigas.tickets.store import get_ticket_store

logger = logging.getLogger(__name__)

SYNC_USER_ENV = "BIGAS_EMAIL_SYNC_USER_EMAIL"


def _sync_user_id() -> str:
    """User id for agent-created tickets (first admin or dev-user)."""
    from bigas.chat.db import get_chat_store

    email = (
        os.environ.get(SYNC_USER_ENV)
        or os.environ.get("CHAT_ADMIN_EMAILS", "").split(",")[0].strip()
        or "dev@localhost"
    )
    store = get_chat_store()
    user = store.find_user_by_email(email)
    if user:
        return user["uid"]
    return "dev-user"


def ticket_url(key: str) -> str:
    return f"/board?ticket={key}"


def ticket_to_api(ticket: Dict[str, Any]) -> Dict[str, Any]:
    key = ticket.get("key") or ""
    return {
        **ticket,
        "url": ticket_url(key),
        "summary": ticket.get("title") or key,
    }


class TicketService:
    def __init__(self) -> None:
        self._store = get_ticket_store()

    def list_boards(self, user_id: str) -> List[Dict[str, Any]]:
        boards = self._store.ensure_default_boards(user_id)
        return [
            {
                **b,
                "columns": columns_for_board(project_key=b.get("project_key")),
                "workflow_enabled": bool(b.get("project_key")),
            }
            for b in boards
        ]

    def create_board(
        self,
        user_id: str,
        *,
        name: str,
        project_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        board = self._store.create_board(user_id, name=name, project_key=project_key)
        return {
            **board,
            "columns": columns_for_board(project_key=board.get("project_key")),
            "workflow_enabled": bool(board.get("project_key")),
        }

    def delete_board(self, board_id: str, *, user_id: str) -> bool:
        return self._store.delete_board(board_id, user_id=user_id)

    def list_tickets(self, board_id: str, *, user_id: str) -> List[Dict[str, Any]]:
        tickets = self._store.list_tickets(board_id, user_id=user_id)
        return [ticket_to_api(t) for t in tickets]

    def create_ticket(
        self,
        board_id: str,
        *,
        user_id: Optional[str] = None,
        title: str,
        description: str = "",
        status: str = "To Do",
        issue_type: str = "Task",
        assignee: Optional[str] = None,
        fix_version: Optional[str] = None,
        marketing: bool = False,
        parent_key: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        ticket = self._store.create_ticket(
            board_id,
            title=title,
            description=description,
            status=status,
            issue_type=issue_type,
            assignee=assignee,
            fix_version=fix_version,
            marketing=marketing,
            parent_key=parent_key,
            thread_id=thread_id,
            user_id=user_id,
        )
        return ticket_to_api(ticket)

    def update_ticket(
        self,
        ticket_id: str,
        *,
        user_id: str,
        previous_status: Optional[str] = None,
        **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        existing = self._store.get_ticket(ticket_id)
        if not existing:
            return None
        old_status = previous_status or existing.get("status")
        ticket = self._store.update_ticket(ticket_id, user_id=user_id, **fields)
        if not ticket:
            return None
        new_status = ticket.get("status")
        if new_status and new_status != old_status:
            self._on_status_change(ticket, old_status=old_status or "", new_status=new_status)
        return ticket_to_api(ticket)

    def create_ticket_for_project(
        self,
        project_key: str,
        *,
        title: str,
        description: str,
        issue_type: str = "Task",
        marketing: bool = False,
        parent_key: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        uid = user_id or _sync_user_id()
        board = self._store.find_board_for_project(project_key, uid)
        if not board:
            board = self._store.create_board(
                uid, name=f"{project_key} Board", project_key=project_key
            )
        ticket = self._store.create_ticket(
            board["board_id"],
            title=title,
            description=description,
            issue_type=issue_type,
            marketing=marketing,
            parent_key=parent_key,
            user_id=uid,
        )
        return ticket_to_api(ticket)

    def transition_to_next(self, issue_key: str) -> Dict[str, Any]:
        from bigas.tickets.jira_adapter import TicketJiraAdapter

        adapter = TicketJiraAdapter()
        result = adapter.transition_issue_to_next(issue_key)
        ticket = self._store.get_ticket_by_key(issue_key)
        if ticket:
            self._on_status_change(
                ticket,
                old_status=result.get("previous_status") or "",
                new_status=result.get("new_status") or "",
            )
        return result

    def _on_status_change(
        self,
        ticket: Dict[str, Any],
        *,
        old_status: str,
        new_status: str,
    ) -> None:
        board = self._store.get_board(ticket.get("board_id") or "")
        if not board or not board.get("project_key"):
            return
        if new_status == old_status:
            return

        project_key = board.get("project_key") or ""

        def _run() -> None:
            try:
                if new_status in AI_TRIGGER_STATUSES:
                    from bigas.tickets.automation import InternalTicketAutomation

                    InternalTicketAutomation().handle_status_change(
                        issue_key=ticket.get("key") or "",
                        to_status=new_status,
                        from_status=old_status,
                        project_key=project_key,
                    )
                elif new_status == "Done" and not ticket.get("done_processed"):
                    self._handle_done(ticket, project_key=project_key)
            except Exception:
                logger.exception(
                    "Ticket automation failed for %s → %s",
                    ticket.get("key"),
                    new_status,
                )

        threading.Thread(target=_run, daemon=True).start()

    def _handle_done(self, ticket: Dict[str, Any], *, project_key: str) -> None:
        from bigas.chat.activity import mirror_to_activity_feed, post_to_agent_thread

        key = ticket.get("key") or ""
        title = ticket.get("title") or key
        msg = f"Ticket **{key}** ({title}) moved to Done on the {project_key} board."
        mirror_to_activity_feed(msg, type_="ticket", source="product")
        post_to_agent_thread(
            "product",
            f"Progress note: {msg}\n\nConsider drafting a progress update or release notes if this completes a batch.",
        )
        self._store.update_ticket(ticket["ticket_id"], done_processed=True)

    def lookup_ticket(self, key: str) -> Optional[Dict[str, Any]]:
        ticket = self._store.get_ticket_by_key(key)
        return ticket_to_api(ticket) if ticket else None

    def lookup_tickets(self, keys: List[str]) -> List[Dict[str, Any]]:
        out = []
        for key in keys:
            ticket = self.lookup_ticket(key)
            if ticket:
                out.append(
                    {
                        "key": ticket["key"],
                        "summary": ticket.get("title") or ticket["key"],
                        "status": ticket.get("status"),
                        "url": ticket.get("url"),
                        "issue_type": ticket.get("issue_type"),
                    }
                )
        return out

    def list_epics(self, project_key: str) -> List[Dict[str, Any]]:
        epics = self._store.list_epics(project_key)
        return [
            {
                "key": e["key"],
                "summary": e.get("title") or e["key"],
                "status": e.get("status"),
                "url": ticket_url(e["key"]),
            }
            for e in epics
        ]
