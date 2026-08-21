"""Shared Jira formatting rules for chat agents and tool result humanization."""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

# Appended to every chat agent system prompt at runtime.
JIRA_FORMATTING_RULES = """
Jira ticket formatting (mandatory):
- Always respond in English. Never use Swedish or any other language.
- When you should file work in Jira, call create_jira_issue yourself (Task or Bug only — never Epics). Never tell the user to create the issue in Jira.
- Pass project_key (e.g. GPWW, VFA, BIG). For marketing/website/SEO/content/ads work, set marketing=true. Use parent_epic_key to link a Task/Bug to an existing goal Epic.
- When creating or referencing a Jira ticket, include the ticket title and a clickable Markdown link: `[Ticket Title](https://<domain>.atlassian.net/browse/TICKET-KEY)`.
- Never output raw JSON or HTML to the user.
- When discussing a Jira ticket, always provide a button to move it to the next workflow column by outputting this exact markdown on its own line:
  `[Move to next column](bigas://action/jira_transition?issue=TICKET-KEY)`
  Replace TICKET-KEY with the actual issue key (e.g. BIG-13).
""".strip()

JIRA_AWARE_AGENT_IDS = frozenset({"chief", "marketing", "product", "cto", "devops"})


def jira_transition_action_markdown(issue_key: str) -> str:
    """Markdown action link rendered as a button in the chat UI."""
    key = (issue_key or "").strip()
    if not key:
        return ""
    return f"[Move to next column](bigas://action/jira_transition?issue={quote(key, safe='')})"


def format_jira_issue_markdown(
    *,
    key: str,
    url: str,
    summary: Optional[str] = None,
    include_transition_button: bool = True,
) -> str:
    """Format a Jira issue as markdown link with optional transition button."""
    issue_key = (key or "").strip()
    browse_url = (url or "").strip()
    title = (summary or issue_key).strip() or issue_key
    if not issue_key:
        return ""
    link = f"[{title}]({browse_url})" if browse_url else issue_key
    if include_transition_button:
        button = jira_transition_action_markdown(issue_key)
        if button:
            return f"{link}\n\n{button}"
    return link


def humanize_jira_tool_result(payload: Dict[str, Any]) -> Optional[str]:
    """Turn create/lookup Jira tool JSON into user-facing markdown."""
    if not payload.get("ok"):
        return None
    key = (payload.get("key") or payload.get("issue_key") or "").strip()
    if not key:
        return None
    url = (payload.get("url") or "").strip()
    summary = (payload.get("summary") or payload.get("title") or key).strip()
    return format_jira_issue_markdown(key=key, url=url, summary=summary)
