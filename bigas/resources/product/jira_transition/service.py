"""Move a Jira issue to the next workflow column (chat UI action)."""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

from bigas.chat.activity import mirror_to_activity_feed
from bigas.chat.jira_formatting import format_jira_issue_markdown
from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
)


class JiraTransitionError(RuntimeError):
    pass


def transition_issue_to_next_column(issue_key: str) -> Dict[str, Any]:
    """Transition issue forward and log to the activity feed."""
    key = (issue_key or "").strip()
    if not key:
        raise JiraTransitionError("issue_key is required")

    from bigas.tickets.config import use_internal_board
    from bigas.tickets.store import get_ticket_store

    if use_internal_board() or get_ticket_store().get_ticket_by_key(key):
        from bigas.tickets.service import TicketService, ticket_url
        from bigas.chat.jira_formatting import format_jira_issue_markdown

        try:
            result = TicketService().transition_to_next(key)
        except Exception as exc:
            raise JiraTransitionError(str(exc)) from exc
        summary = (result.get("summary") or key).strip()
        new_status = (result.get("new_status") or "").strip()
        link = format_jira_issue_markdown(
            key=key,
            url=ticket_url(key),
            summary=summary,
            include_transition_button=False,
        )
        activity_message = f"Moved {link} to {new_status}"
        try:
            mirror_to_activity_feed(activity_message, type_="ticket", source="product")
        except Exception:
            logger.exception("Failed to mirror ticket transition to activity feed")
        return {
            "success": True,
            "message": result.get("message") or f"Moved to {new_status}",
            "new_status": new_status,
            "issue_key": key,
            "previous_status": result.get("previous_status"),
        }

    try:
        client = JiraClient(JiraConfig.from_env())
        result = client.transition_issue_to_next(key)
    except JiraError as exc:
        raise JiraTransitionError(str(exc)) from exc

    summary = (result.get("summary") or key).strip()
    new_status = (result.get("new_status") or "").strip()
    browse_url = (result.get("url") or "").strip()
    link = format_jira_issue_markdown(
        key=key,
        url=browse_url,
        summary=summary,
        include_transition_button=False,
    )
    activity_message = f"Moved {link} to {new_status}"
    try:
        mirror_to_activity_feed(activity_message, type_="jira", source="product")
    except Exception:
        logger.exception("Failed to mirror Jira transition to activity feed")

    return {
        "success": True,
        "message": result.get("message") or f"Moved to {new_status}",
        "new_status": new_status,
        "issue_key": key,
        "previous_status": result.get("previous_status"),
    }
