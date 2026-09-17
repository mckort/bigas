"""Return an existing open ticket when create_ticket would duplicate title/work."""
from __future__ import annotations

from typing import Any, Dict, Optional

from bigas.okr.plan import titles_are_same_work


def find_open_duplicate_internal(
    project_key: str,
    title: str,
    issue_type: str,
) -> Optional[Dict[str, Any]]:
    from bigas.tickets.service import ticket_to_api
    from bigas.tickets.store import get_ticket_store

    proj = (project_key or "").strip().upper()
    if not proj:
        return None
    if not (title or "").strip():
        return None
    itype = str(issue_type or "Task").strip().title() or "Task"
    store = get_ticket_store()
    for ticket in store.list_tickets_by_project(proj, issue_type=itype):
        status = (ticket.get("status") or "").strip().lower()
        if (
            status in {"done", "closed", "cancelled", "canceled", "rejected"}
            or ticket.get("statusCategory") == "Done"
        ):
            continue
        existing_title = str(ticket.get("title") or "")
        if titles_are_same_work(title, existing_title):
            return ticket_to_api(ticket)
    return None


def find_open_duplicate_jira(
    client: Any,
    *,
    project_key: str,
    title: str,
    issue_type: str,
) -> Optional[Dict[str, Any]]:
    from bigas.resources.product.create_release_notes.jira_client import (
        JiraError,
        compact_jira_issue,
    )

    proj = (project_key or "").strip().upper()
    if not proj:
        return None
    if not (title or "").strip():
        return None
    itype = str(issue_type or "Task").strip().title() or "Task"
    safe_type = itype.replace("\\", "\\\\").replace('"', '\\"')
    jql = (
        f"project = {proj} "
        f'AND issuetype = "{safe_type}" '
        f"AND statusCategory != Done "
        f"ORDER BY updated DESC"
    )
    search = getattr(client, "search_jql", None)
    if not callable(search):
        return None
    try:
        raw = search(
            jql=jql,
            fields=["summary", "status", "issuetype", "project"],
            max_results_per_page=50,
            max_pages=4,
        )
    except JiraError:
        return None
    for issue in raw or []:
        fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
        summary = str(fields.get("summary") or "")
        if titles_are_same_work(title, summary):
            base_url = getattr(getattr(client, "_config", None), "base_url", None) or getattr(
                client, "base_url", ""
            )
            return compact_jira_issue(issue, base_url=base_url)
    return None
