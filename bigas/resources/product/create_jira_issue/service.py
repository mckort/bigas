from __future__ import annotations

from typing import Any, Dict, List, Optional

from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
    normalize_parent_epic_key,
    normalize_project_keys,
)

ALLOWED_ISSUE_TYPES = frozenset({"Task", "Bug"})
_MARKETING_LABEL = "marketing"


class CreateJiraIssueError(RuntimeError):
    pass


def _format_jira_error(exc: JiraError, *, project_key: str) -> str:
    msg = str(exc)
    lower = msg.lower()
    if "404" in msg or "does not exist" in lower or "not found" in lower:
        if project_key:
            return f"Project key {project_key!r} not found or not accessible."
    if "401" in msg or "403" in msg or "authentication" in lower or "authorization" in lower:
        return (
            "Jira authentication or authorization failed. "
            "Check JIRA_EMAIL and JIRA_API_TOKEN."
        )
    return msg


class CreateJiraIssueService:
    """Create Jira issues via the shared JiraClient, or internal board when Jira is off."""

    def create(
        self,
        *,
        project_key: str,
        summary: str,
        description: str,
        issue_type: str = "Task",
        marketing: bool = False,
        parent_epic_key: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from bigas.tickets.config import use_internal_board

        keys = normalize_project_keys(project_key)
        if not keys:
            raise CreateJiraIssueError("project_key is required")
        proj = keys[0]

        title = str(summary or "").strip()
        if not title:
            raise CreateJiraIssueError("summary is required")

        body = str(description or "").strip()
        if not body:
            raise CreateJiraIssueError("description is required")

        itype = str(issue_type or "Task").strip().title() or "Task"
        if itype not in ALLOWED_ISSUE_TYPES:
            allowed = ", ".join(sorted(ALLOWED_ISSUE_TYPES))
            raise CreateJiraIssueError(
                f"issue_type must be one of: {allowed} (got {issue_type!r})"
            )

        if use_internal_board():
            from bigas.tickets.service import TicketService

            epic = normalize_parent_epic_key(parent_epic_key, project_key=proj)
            ticket = TicketService().create_ticket_for_project(
                proj,
                title=title,
                description=body,
                issue_type=itype,
                marketing=marketing,
                labels=[_MARKETING_LABEL] if marketing else None,
                parent_key=epic,
                user_id=user_id,
            )
            out: Dict[str, Any] = {
                "ok": True,
                "key": ticket.get("key"),
                "url": ticket.get("url"),
                "summary": ticket.get("title") or title,
                "issue_type": itype,
                "project_key": proj,
                "source": "internal_board",
            }
            if ticket.get("labels"):
                out["labels"] = ticket.get("labels")
            if epic:
                out["parent_epic_key"] = epic
            return out

        labels: Optional[List[str]] = None
        if marketing:
            labels = [_MARKETING_LABEL]

        try:
            client = JiraClient(JiraConfig.from_env())
            epic = normalize_parent_epic_key(parent_epic_key, project_key=proj)
            result = client.create_issue(
                project_key=proj,
                summary=title,
                description_markdown=body,
                issue_type=itype,
                labels=labels,
                parent_epic_key=epic,
            )
            out = {
                "ok": True,
                "key": result.get("key"),
                "url": result.get("url"),
                "issue_type": itype,
                "project_key": proj,
            }
            if labels:
                out["labels"] = labels
            if result.get("parent_dropped"):
                out["parent_dropped"] = True
            elif result.get("parent_epic_key") or epic:
                out["parent_epic_key"] = result.get("parent_epic_key") or epic
            return out
        except JiraError as e:
            raise CreateJiraIssueError(_format_jira_error(e, project_key=proj)) from e
