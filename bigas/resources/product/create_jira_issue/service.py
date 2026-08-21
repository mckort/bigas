from __future__ import annotations

from typing import Any, Dict, List, Optional

from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
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
    """Create Jira issues via the shared JiraClient."""

    def create(
        self,
        *,
        project_key: str,
        summary: str,
        description: str,
        issue_type: str = "Task",
        marketing: bool = False,
    ) -> Dict[str, Any]:
        keys = normalize_project_keys(project_key)
        if not keys:
            raise CreateJiraIssueError("project_key is required")
        proj = keys[0]

        title = (summary or "").strip()
        if not title:
            raise CreateJiraIssueError("summary is required")

        body = (description or "").strip()
        if not body:
            raise CreateJiraIssueError("description is required")

        itype = (issue_type or "Task").strip() or "Task"
        if itype not in ALLOWED_ISSUE_TYPES:
            allowed = ", ".join(sorted(ALLOWED_ISSUE_TYPES))
            raise CreateJiraIssueError(
                f"issue_type must be one of: {allowed} (got {issue_type!r})"
            )

        labels: Optional[List[str]] = None
        if marketing:
            labels = [_MARKETING_LABEL]

        try:
            client = JiraClient(JiraConfig.from_env())
            result = client.create_issue(
                project_key=proj,
                summary=title,
                description_markdown=body,
                issue_type=itype,
                labels=labels,
            )
            return {
                "ok": True,
                "key": result.get("key"),
                "url": result.get("url"),
                "issue_type": itype,
                "project_key": proj,
                **(
                    {"labels": labels}
                    if labels
                    else {}
                ),
            }
        except JiraError as e:
            raise CreateJiraIssueError(_format_jira_error(e, project_key=proj)) from e
