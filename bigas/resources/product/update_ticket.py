from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
    adf_to_plain_text,
    normalize_issue_key,
)
from bigas.tickets.constants import resolve_column_status

logger = logging.getLogger(__name__)


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


def _jira_snapshot(
    client: JiraClient,
    *,
    key: str,
    base_url: str,
    resolved_status: Optional[str],
    written_description: Optional[str],
) -> Dict[str, Any]:
    """Same keys as the internal-board payload, including unchanged fields.

    ``written_description`` is None when this call did not replace the body.
    A failed re-read keeps the values this call wrote and still returns
    ``status`` and ``description``.
    """
    summary: Optional[str] = None
    status = resolved_status
    description = "" if written_description is None else written_description
    try:
        issue = client.get_issue(
            key,
            fields=["summary", "status", "description"],
        )
    except JiraError as exc:
        logger.warning(
            "Updated Jira issue %s but could not re-read it: %s",
            key,
            exc,
        )
    else:
        fields: Dict[str, Any] = {}
        if isinstance(issue, dict):
            raw_fields = issue.get("fields")
            if isinstance(raw_fields, dict):
                fields = raw_fields
        fetched_summary = str(fields.get("summary") or "").strip()
        if fetched_summary:
            summary = fetched_summary
        status_field = fields.get("status")
        status_name = ""
        if isinstance(status_field, dict):
            status_name = str(status_field.get("name") or "").strip()
        if status_name:
            status = status_name
        if written_description is None:
            description = adf_to_plain_text(fields.get("description"))
    root = (base_url or "").rstrip("/")
    return {
        "ok": True,
        "key": key,
        "url": f"{root}/browse/{key}" if root else None,
        "summary": summary or key,
        "status": status,
        "description": description or "",
        "source": "jira",
    }


class UpdateTicketService:
    """Update board column and/or description on an existing ticket.

    The internal board and Jira both return ``status`` and ``description``
    (current values, including fields this call did not change).
    """

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
            config = JiraConfig.from_env()
            client = JiraClient(config)
            if raw_status:
                resolved_status = resolve_column_status(raw_status, project_key=proj) or raw_status
                client.transition_issue(key, to_status_name=resolved_status)
            if has_description:
                client.update_description(key, str(description or ""))
        except JiraError as exc:
            raise UpdateTicketError(_format_jira_error(exc, issue_key=key)) from exc
        else:
            return _jira_snapshot(
                client,
                key=key,
                base_url=config.base_url,
                resolved_status=resolved_status,
                written_description=str(description or "") if has_description else None,
            )
