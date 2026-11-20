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


def _result_from_ticket(updated: Dict[str, Any], *, key: str) -> Dict[str, Any]:
    return {
        "ok": True,
        "key": updated.get("key") or key,
        "url": updated.get("url"),
        "summary": updated.get("title") or updated.get("summary") or key,
        "status": updated.get("status"),
        "description": updated.get("description") or "",
        "source": "internal_board",
    }


class UpdateTicketService:
    """Update board column and/or description on an existing ticket."""

    def update(
        self,
        *,
        issue_key: str,
        status: Optional[str] = None,
        description: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        key = normalize_issue_key(issue_key)
        if not key:
            raise UpdateTicketError("issue_key is required")
        raw_status = str(status or "").strip()
        has_description = description is not None
        if not raw_status and not has_description:
            raise UpdateTicketError("status or description is required")

        from bigas.tickets.config import use_internal_board
        from bigas.tickets.store import get_ticket_store

        ticket = get_ticket_store().get_ticket_by_key(key)
        if use_internal_board() or ticket:
            from bigas.tickets.service import TicketService

            svc = TicketService()
            updated: Optional[Dict[str, Any]] = None
            try:
                if raw_status:
                    updated = svc.set_status(key, raw_status, user_id=user_id)
                if has_description:
                    updated = svc.set_description(
                        key,
                        str(description or ""),
                        user_id=user_id,
                    )
            except ValueError as exc:
                raise UpdateTicketError(str(exc)) from exc
            if not updated:
                raise UpdateTicketError(f"Ticket {key} not found")
            return _result_from_ticket(updated, key=key)

        from bigas.portfolio import project_key_from_issue_key

        proj = project_key_from_issue_key(key)
        resolved_status: Optional[str] = None
        try:
            client = JiraClient(JiraConfig.from_env())
            if raw_status:
                resolved_status = resolve_column_status(raw_status, project_key=proj) or raw_status
                client.transition_issue(key, to_status_name=resolved_status)
            if has_description:
                client.update_description(key, str(description or ""))
        except JiraError as exc:
            raise UpdateTicketError(_format_jira_error(exc, issue_key=key)) from exc
        payload: Dict[str, Any] = {
            "ok": True,
            "key": key,
            "source": "jira",
        }
        if resolved_status is not None:
            payload["status"] = resolved_status
        if has_description:
            payload["description"] = str(description or "")
        return payload
