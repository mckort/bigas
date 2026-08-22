from __future__ import annotations

from typing import Any, Dict, Optional

from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
    adf_to_plain_text,
    compact_jira_issue,
    issue_lookup_fields,
    normalize_issue_key,
    normalize_project_keys,
)
from bigas.resources.product.jira_automation.comments import format_human_comments

LOOKUP_DESCRIPTION_MAX_CHARS = 8000
LOOKUP_COMMENT_MAX = 20


class LookupJiraError(RuntimeError):
    pass


def _format_jira_error(exc: JiraError, *, issue_key: str = "", project_key: str = "") -> str:
    msg = str(exc)
    lower = msg.lower()
    if "404" in msg or "does not exist" in lower or "not found" in lower:
        if issue_key:
            return f"Issue {issue_key!r} not found or not accessible."
        if project_key:
            return f"Project key {project_key!r} not found or not accessible."
    if "401" in msg or "403" in msg or "authentication" in lower or "authorization" in lower:
        return (
            "Jira authentication or authorization failed. "
            "Check JIRA_EMAIL and JIRA_API_TOKEN."
        )
    return msg


def _clip(text: str, max_chars: int) -> str:
    body = (text or "").strip()
    if max_chars > 0 and len(body) > max_chars:
        return body[: max_chars - 3].rstrip() + "..."
    return body


class LookupJiraService:
    """Read-only Jira lookup for chat agents: one issue (with parent) and/or open Epics."""

    def lookup(
        self,
        *,
        issue_key: Optional[str] = None,
        project_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        issue = normalize_issue_key(issue_key)
        projects = normalize_project_keys(project_key)
        if not issue and not projects:
            raise LookupJiraError("issue_key or project_key is required")

        try:
            client = JiraClient(JiraConfig.from_env())
            out: Dict[str, Any] = {
                "ok": True,
                "parent_guidance": (
                    "A referenced ticket's parent is context only. "
                    "Set parent_epic_key on create_jira_issue only if the new work "
                    "belongs under that Epic's goal; otherwise create a standalone Task/Bug."
                ),
            }
            if issue:
                raw = client.get_issue(issue, fields=issue_lookup_fields())
                compact = compact_jira_issue(raw, base_url=client._config.base_url)
                fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
                description = _clip(
                    adf_to_plain_text(fields.get("description")),
                    LOOKUP_DESCRIPTION_MAX_CHARS,
                )
                if description:
                    compact["description"] = description
                try:
                    raw_comments = client.list_comments(issue, max_results=50)
                except JiraError:
                    raw_comments = []
                comments_text = format_human_comments(
                    raw_comments,
                    max_comments=LOOKUP_COMMENT_MAX,
                )
                compact["human_comments"] = comments_text
                out["issue"] = compact
                if compact.get("parent"):
                    out["parent"] = compact["parent"]
                proj = compact.get("project_key") or ""
                if proj and proj not in projects:
                    projects.append(proj)
            if projects:
                proj = projects[0]
                out["project_key"] = proj
                out["epics"] = client.list_open_epics(proj)
            return out
        except JiraError as e:
            raise LookupJiraError(
                _format_jira_error(
                    e,
                    issue_key=issue or "",
                    project_key=(projects[0] if projects else ""),
                )
            ) from e
