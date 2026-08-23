"""JiraClient-compatible adapter for internal tickets (AI automation handlers)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from bigas.jira_exceptions import JiraError
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

    def _format_issue(
        self,
        ticket: Dict[str, Any],
        *,
        issue_key: Optional[str] = None,
        board: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        key = issue_key or ticket["key"]
        if board is None:
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
                "summary": ticket.get("title") or key,
                "description": ticket.get("description") or "",
                "status": {"name": ticket.get("status") or "To Do"},
                "issuetype": {"name": ticket.get("issue_type") or "Task"},
                "project": {"key": (proj or "PERS").upper()},
                "labels": labels,
                "parent": parent,
                "issuelinks": [],
            },
        }

    def get_issue(
        self,
        issue_key: str,
        *,
        fields: Optional[List[str]] = None,
        expand: Optional[str] = None,
    ) -> Dict[str, Any]:
        ticket = self._ticket(issue_key)
        if not ticket:
            raise JiraError(f"Ticket {issue_key} not found")
        return self._format_issue(ticket, issue_key=issue_key)

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
            out.append(
                {
                    "id": c.get("id"),
                    "body": c.get("body"),
                    "created": c.get("created_at"),
                    "author": {"displayName": "Bigas"},
                }
            )
        return out

    def get_epics_by_statuses(
        self,
        *,
        statuses: Optional[List[str]] = None,
        project_keys: Optional[List[str]] = None,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        wanted = {str(s).strip().lower() for s in (statuses or []) if str(s).strip()}
        projects = {str(k).strip().upper() for k in (project_keys or []) if str(k).strip()}
        epics = []
        board_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        for ticket in self._store.list_all_epics():
            board_id = ticket.get("board_id") or ""
            if board_id not in board_cache:
                board_cache[board_id] = self._store.get_board(board_id) if board_id else None
            board = board_cache[board_id]
            proj = ((board or {}).get("project_key") or ticket.get("project_key") or "").upper()
            if projects and proj not in projects:
                continue
            status = (ticket.get("status") or "").strip()
            if wanted and status.lower() not in wanted:
                continue
            epics.append(self._format_issue(ticket, board=board))
        return epics

    def get_issues_for_epic(
        self,
        epic_key: str,
        *,
        status_clause: str = "",
        updated_since_days: Optional[int] = None,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        children = self._store.list_tickets_for_parent(epic_key)
        if updated_since_days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=int(updated_since_days))
            kept = []
            for ticket in children:
                updated = _ticket_updated_at(ticket)
                if updated is not None and updated >= cutoff:
                    kept.append(ticket)
            children = kept
        clause = (status_clause or "").strip().lower()
        out: List[Dict[str, Any]] = []
        for ticket in children:
            status = (ticket.get("status") or "").strip()
            if not _status_matches_clause(status, clause):
                continue
            out.append(self._format_issue(ticket))
        return out

    def add_comment(self, issue_key: str, body_text: str) -> Dict[str, Any]:
        ticket = self._ticket(issue_key)
        if not ticket:
            raise RuntimeError(f"Ticket {issue_key} not found")
        comment = self._store.add_comment(ticket["ticket_id"], body_text)
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


def _ticket_updated_at(ticket: Dict[str, Any]) -> Optional[datetime]:
    raw = ticket.get("updated_at") or ticket.get("created_at")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        parsed = raw
    else:
        text = str(raw).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _normalize_jql_clause(clause: str) -> str:
    return " ".join((clause or "").strip().lower().split())


def _status_matches_clause(status: str, clause: str) -> bool:
    normalized = _normalize_jql_clause(clause)
    if not normalized:
        return True
    name = (status or "").strip()
    lower = name.lower()
    equals = re.search(r"status\s*=\s*([^,\s]+(?:\s+[^,\s]+)*)", normalized, re.IGNORECASE)
    if equals:
        return lower == equals.group(1).strip().lower()
    not_equals = re.search(r"status\s*!=\s*([^,\s]+(?:\s+[^,\s]+)*)", normalized, re.IGNORECASE)
    if not_equals:
        return lower != not_equals.group(1).strip().lower()
    if re.search(r"in\s+progress", normalized, re.IGNORECASE):
        return "in progress" in lower
    return True
