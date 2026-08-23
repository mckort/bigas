"""JiraClient-compatible adapter for internal tickets (AI automation handlers)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from bigas.tickets.constants import next_column
from bigas.tickets.store import get_ticket_store


class TicketJiraAdapter:
    """
    Mimics JiraClient methods used by AI column handlers so existing
    research/design/implement code can run against internal tickets.
    """

    def __init__(self) -> None:
        self._store = get_ticket_store()

    def _ticket(self, issue_key: str) -> Optional[Dict[str, Any]]:
        return self._store.get_ticket_by_key(issue_key)

    def _board(self, ticket: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self._store.get_board(ticket.get("board_id") or "")

    def get_issue(
        self,
        issue_key: str,
        *,
        fields: Optional[List[str]] = None,
        expand: Optional[str] = None,
    ) -> Dict[str, Any]:
        ticket = self._ticket(issue_key)
        if not ticket:
            raise RuntimeError(f"Ticket {issue_key} not found")
        board = self._board(ticket)
        proj = board.get("project_key") if board else ticket.get("project_key")
        parent_key = ticket.get("parent_key")
        parent = None
        if parent_key:
            parent_ticket = self._store.get_ticket_by_key(parent_key)
            if parent_ticket:
                parent = {
                    "key": parent_key,
                    "fields": {"summary": parent_ticket.get("title")},
                }
        labels = []
        if ticket.get("marketing"):
            labels.append({"name": "marketing"})
        return {
            "key": ticket["key"],
            "fields": {
                "summary": ticket.get("title") or issue_key,
                "description": ticket.get("description") or "",
                "status": {"name": ticket.get("status") or "To Do"},
                "issuetype": {"name": ticket.get("issue_type") or "Task"},
                "project": {"key": (proj or "PERS").upper()},
                "labels": labels,
                "parent": parent,
                "issuelinks": [],
            },
        }

    def list_comments(
        self,
        issue_key: str,
        *,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        ticket = self._ticket(issue_key)
        if not ticket:
            return []
        comments = self._store.list_comments(ticket["ticket_id"])
        out = []
        for c in comments[:max_results]:
            name = (c.get("author_name") or "").strip() or "Bigas"
            out.append(
                {
                    "id": c.get("id"),
                    "body": c.get("body"),
                    "created": c.get("created_at"),
                    "author": {"displayName": name},
                }
            )
        return out

    def add_comment(self, issue_key: str, body_text: str) -> Dict[str, Any]:
        ticket = self._ticket(issue_key)
        if not ticket:
            raise RuntimeError(f"Ticket {issue_key} not found")
        comment = self._store.add_comment(
            ticket["ticket_id"],
            body_text,
            author_name="Bigas",
        )
        return comment or {}

    def update_description(self, issue_key: str, description_markdown: str) -> None:
        ticket = self._ticket(issue_key)
        if not ticket:
            raise RuntimeError(f"Ticket {issue_key} not found")
        self._store.update_ticket(
            ticket["ticket_id"],
            description=description_markdown or "",
        )

    def transition_issue(
        self,
        issue_key: str,
        *,
        to_status_name: str,
        comment: Optional[str] = None,
    ) -> None:
        ticket = self._ticket(issue_key)
        if not ticket:
            raise RuntimeError(f"Ticket {issue_key} not found")
        board = self._board(ticket)
        proj = board.get("project_key") if board else None
        target = (to_status_name or "").strip()
        if not target:
            raise RuntimeError("to_status_name is required")
        self._store.update_ticket(ticket["ticket_id"], status=target)
        if comment:
            self.add_comment(issue_key, comment)

    def transition_issue_to_next(self, issue_key: str) -> Dict[str, Any]:
        ticket = self._ticket(issue_key)
        if not ticket:
            raise RuntimeError(f"Ticket {issue_key} not found")
        board = self._board(ticket)
        proj = board.get("project_key") if board else None
        current = ticket.get("status") or "To Do"
        nxt = next_column(current, project_key=proj)
        if not nxt:
            raise RuntimeError(f"No next column after {current!r} for {issue_key}")
        self._store.update_ticket(ticket["ticket_id"], status=nxt)
        return {
            "ok": True,
            "issue_key": issue_key,
            "summary": ticket.get("title") or issue_key,
            "url": f"/board?ticket={issue_key}",
            "previous_status": current,
            "new_status": nxt,
            "message": f"Moved {issue_key} to {nxt}",
        }
