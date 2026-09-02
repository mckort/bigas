"""JiraClient-compatible adapter for internal tickets (AI automation handlers)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from bigas.jira_exceptions import JiraError
from bigas.resources.product.release_workflow import active_fix_version_from_env
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
        from bigas.tickets.labels import resolve_ticket_labels

        labels = resolve_ticket_labels(ticket)
        assignee = (ticket.get("assignee") or "").strip()
        fix_version = (ticket.get("fix_version") or "").strip() or None
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
                "assignee": {"displayName": assignee} if assignee else None,
                "resolutiondate": (ticket.get("done_at") or "").strip() or None,
                "updated": ticket.get("updated_at") or ticket.get("created_at"),
                "fixVersions": [{"name": fix_version}] if fix_version else [],
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

    def list_attachments(self, issue_key: str) -> List[Dict[str, Any]]:
        ticket = self._ticket(issue_key)
        if not ticket:
            return []
        return list(ticket.get("attachments") or self._store.list_attachments(ticket["ticket_id"]))

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

    def search_issues_done_in_last_n_days(
        self,
        *,
        days: int = 14,
        jql_extra: str = "",
        project_keys: Optional[List[str]] = None,
        fields: Optional[List[str]] = None,
        max_results_per_page: int = 50,
        max_pages: int = 50,
    ) -> List[Dict[str, Any]]:
        """Return Done tickets whose done_at (else updated_at) falls in the last N days."""
        del jql_extra, fields, max_results_per_page, max_pages
        if days < 1 or days > 365:
            raise ValueError("days must be between 1 and 365")
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
        tickets = self._store.list_tickets_by_status("Done", project_keys=project_keys)
        out: List[Dict[str, Any]] = []
        for ticket in tickets:
            stamp = _ticket_done_at(ticket)
            if stamp is None or stamp < cutoff:
                continue
            out.append(self._format_issue(ticket))
        out.sort(
            key=lambda issue: str(
                (issue.get("fields") or {}).get("resolutiondate")
                or (issue.get("fields") or {}).get("updated")
                or ""
            ),
            reverse=True,
        )
        return out

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

    def list_project_versions(self, project_key: str) -> List[Dict[str, Any]]:
        from bigas.tickets.release_store import get_release_store

        items = get_release_store().list_releases(project_key)
        return [
            {
                "id": item.get("release_id"),
                "name": item.get("name"),
                "released": bool(item.get("released")),
                "releaseDate": item.get("released_at"),
            }
            for item in items
        ]

    def get_active_fix_version(self, project_key: str) -> Optional[Dict[str, Any]]:
        from bigas.tickets.releases import default_fix_version

        name = default_fix_version(project_key)
        if not name:
            return None
        return {"name": name, "released": False}

    def search_issues_by_fix_version(
        self,
        *,
        fix_version: str,
        jql_extra: str = "",
        project_keys: Optional[List[str]] = None,
        fields: Optional[List[str]] = None,
        max_results_per_page: int = 50,
        max_pages: int = 50,
    ) -> List[Dict[str, Any]]:
        del jql_extra, fields, max_results_per_page, max_pages
        wanted = (fix_version or "").strip()
        keys = [
            str(k).strip().upper()
            for k in (project_keys or [])
            if str(k).strip()
        ]
        tickets: List[Dict[str, Any]] = []
        for key in keys:
            tickets.extend(self._store.list_tickets_by_project(key))
        out = []
        for ticket in tickets:
            if (ticket.get("fix_version") or "").strip() != wanted:
                continue
            out.append(self._format_issue(ticket))
        return out

    def mark_fix_version_released(
        self,
        *,
        project_key: str,
        version_name: str,
        release_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        del release_date
        from bigas.tickets.releases import ReleaseError, close_release

        try:
            result = close_release(
                project_key,
                version_name,
                create_github=False,
            )
        except ReleaseError as exc:
            raise JiraError(str(exc)) from exc
        return {
            "ok": True,
            "project_key": (project_key or "").strip().upper(),
            "version_name": version_name,
            "moved": result.get("moved") or [],
        }

    def ensure_issue_fix_version(
        self,
        issue_key: str,
        *,
        project_key: Optional[str] = None,
    ) -> Optional[str]:
        """Assign the board default (or env fallback) when ticket has no fix_version."""
        from bigas.tickets.releases import default_fix_version

        ticket = self._ticket(issue_key)
        if not ticket:
            raise JiraError(f"Ticket {issue_key} not found")

        existing = (ticket.get("fix_version") or "").strip()
        if existing:
            return existing

        board = self._board(ticket)
        proj = (
            (project_key or "").strip().upper()
            or (board.get("project_key") if board else None)
            or ticket.get("project_key")
            or ""
        ).strip().upper()
        if not proj and issue_key and "-" in issue_key:
            proj = issue_key.split("-", 1)[0].upper()

        active = default_fix_version(proj) or active_fix_version_from_env(proj)
        if not active:
            return None

        self._store.update_ticket(ticket["ticket_id"], fix_version=active)
        return active

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


def _ticket_updated_at(ticket: Dict[str, Any]) -> Optional[datetime]:
    return _parse_ticket_datetime(ticket.get("updated_at") or ticket.get("created_at"))


def _ticket_done_at(ticket: Dict[str, Any]) -> Optional[datetime]:
    """When the ticket was moved to Done; fall back to updated_at for older rows."""
    return _parse_ticket_datetime(
        ticket.get("done_at") or ticket.get("updated_at") or ticket.get("created_at")
    )


def _parse_ticket_datetime(raw: Any) -> Optional[datetime]:
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
