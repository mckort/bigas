from __future__ import annotations

from typing import Any, Dict, Optional

from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
    normalize_issue_key,
)
from bigas.tickets.constants import resolve_column_status


class UpdateTicketError(RuntimeError):
    pass


def _format_jira_error(exc: JiraError, *, issue_key: str) -> str:
    msg = str(exc)
    lower = msg.lower()
    if "404" in msg or "does not exist" in lower or "not found" in lower:
        return f"Issue {issue_key!r} not found or not accessible."
    if "401" in msg or "403" in msg or "authentication" in lower or "authorization" in lower:
        return (
            "Jira authentication or authorization failed. "
            "Check JIRA_EMAIL and JIRA_API_TOKEN."
        )
    return msg


class UpdateTicketService:
    """Set board column (or Jira status) on an existing ticket."""

    def update(
        self,
        *,
        issue_key: str,
        status: str,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        key = normalize_issue_key(issue_key)
        if not key:
            raise UpdateTicketError("issue_key is required")
        raw = str(status or "").strip()
        if not raw:
            raise UpdateTicketError("status is required")

        from bigas.tickets.config import use_internal_board
        from bigas.tickets.store import get_ticket_store

        ticket = get_ticket_store().get_ticket_by_key(key)
        if use_internal_board() or ticket:
            from bigas.tickets.service import TicketService

            try:
                updated = TicketService().set_status(key, raw, user_id=user_id)
            except ValueError as exc:
                raise UpdateTicketError(str(exc)) from exc
            return {
                "ok": True,
                "key": updated.get("key") or key,
                "url": updated.get("url"),
                "summary": updated.get("title") or updated.get("summary") or key,
                "status": updated.get("status"),
                "source": "internal_board",
            }

        proj = key.split("-", 1)[0]
        resolved = resolve_column_status(raw, project_key=proj) or raw
        try:
            client = JiraClient(JiraConfig.from_env())
            client.transition_issue(key, to_status_name=resolved)
        except JiraError as exc:
            raise UpdateTicketError(_format_jira_error(exc, issue_key=key)) from exc
        return {
            "ok": True,
            "key": key,
            "status": resolved,
            "source": "jira",
        }
