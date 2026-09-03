"""Shared Jira formatting rules for chat agents and tool result humanization."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

# Appended to every chat agent system prompt at runtime.
JIRA_FORMATTING_RULES = """
Jira ticket formatting (mandatory):
- Reply in the user's language.
- When you should file work on the internal Bigas board (or Jira), call create_ticket yourself (Task or Bug only — never Epics). Never tell the user to create the issue themselves.
- Pass project_key (e.g. GPWW, VFA, BIG). For marketing/website/SEO/content/ads work, set marketing=true.
- To put a new ticket in a column, pass status on create_ticket (e.g. "Final Review"). To move an existing ticket, call update_ticket with issue_key and status. Do not tell the user to drag the card.
- Use lookup_ticket when you need issue details or a project's open Epics. issue_key accepts several keys or a range (BIG-15 to BIG-18). Do not ask the user for an Epic key if you can look it up.
- Use search_tickets with JQL when the user described a filter (status, type, text) without naming keys. Do not invent issue keys.
- After lookup_ticket, search_tickets, or any tool, answer the user's question in your own words. Never reply with only ticket links, Open Epics, or a Move button.
- A ticket you looked up does not mean the new work belongs under the same Epic. Set parent_epic_key only when the new Task/Bug clearly belongs under that Epic's goal. Otherwise omit parent_epic_key and create a standalone ticket — that is valid and often correct. Never invent a parent, and never use a Task or Bug as parent.
- When creating or referencing a ticket, include the ticket title and a clickable Markdown link. For Jira: `[Ticket Title](https://<domain>.atlassian.net/browse/TICKET-KEY)`. For the internal board: `[Ticket Title](/board?ticket=TICKET-KEY)`.
- Never output raw JSON or HTML to the user.
- When discussing a ticket, always provide a button to move it to the next workflow column by outputting this exact markdown on its own line:
  `[Move to next column](bigas://action/jira_transition?issue=TICKET-KEY)`
  Replace TICKET-KEY with the actual issue key (e.g. BIG-13).
""".strip()

JIRA_AWARE_AGENT_IDS = frozenset({"chief", "marketing", "product", "cto", "cfo", "devops"})


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
    issues = payload.get("issues")
    issue = payload.get("issue")
    epics = payload.get("epics")
    if (
        payload.get("jql") is not None
        and isinstance(issues, list)
        and not issues
        and not isinstance(issue, dict)
        and not isinstance(epics, list)
    ):
        return "No matching issues."
    if isinstance(issues, list) or isinstance(issue, dict) or isinstance(epics, list):
        return _humanize_lookup_result(
            issue if isinstance(issue, dict) else None,
            epics,
            payload,
            issues if isinstance(issues, list) else None,
        )
    key = (payload.get("key") or payload.get("issue_key") or "").strip()
    if not key:
        return None
    url = (payload.get("url") or "").strip()
    summary = (payload.get("summary") or payload.get("title") or key).strip()
    return format_jira_issue_markdown(key=key, url=url, summary=summary)


def _format_lookup_issue_line(
    issue: Dict[str, Any],
    *,
    include_transition_button: bool,
) -> str:
    key = str(issue.get("key") or "").strip()
    if not key:
        return ""
    status = str(issue.get("status") or "").strip()
    stamp = str(issue.get("done_at") or issue.get("updated") or issue.get("created") or "").strip()
    date_bit = f" ({stamp[:10]})" if stamp else ""
    link = format_jira_issue_markdown(
        key=key,
        url=str(issue.get("url") or "").strip(),
        summary=str(issue.get("summary") or key).strip(),
        include_transition_button=include_transition_button,
    )
    if status and not include_transition_button:
        return f"{link} — {status}{date_bit}"
    if status:
        return f"{link}\nStatus: {status}{date_bit}"
    return f"{link}{date_bit}"


def _humanize_lookup_result(
    issue: Optional[Dict[str, Any]],
    epics: Any,
    payload: Dict[str, Any],
    issues: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    lines: List[str] = []
    issue_rows = [row for row in (issues or []) if isinstance(row, dict) and (row.get("key") or "").strip()]
    if len(issue_rows) > 1:
        for row in issue_rows:
            line = _format_lookup_issue_line(row, include_transition_button=False)
            if line:
                lines.append(f"- {line}")
        missing = payload.get("missing")
        if isinstance(missing, list) and missing:
            lines.append("Missing: " + ", ".join(str(k) for k in missing if k))
    elif issue and (issue.get("key") or "").strip():
        lines.append(_format_lookup_issue_line(issue, include_transition_button=True))
        parent = issue.get("parent") if isinstance(issue.get("parent"), dict) else payload.get("parent")
        if isinstance(parent, dict) and (parent.get("key") or "").strip():
            pkey = str(parent.get("key") or "").strip()
            parent_link = format_jira_issue_markdown(
                key=pkey,
                url=str(parent.get("url") or "").strip(),
                summary=str(parent.get("summary") or pkey).strip(),
                include_transition_button=False,
            )
            itype = str(parent.get("issue_type") or "parent").strip() or "parent"
            lines.append(f"Parent ({itype}): {parent_link}")
    if isinstance(epics, list):
        epic_lines = []
        for epic in epics:
            if not isinstance(epic, dict) or not (epic.get("key") or "").strip():
                continue
            ekey = str(epic.get("key") or "").strip()
            epic_lines.append(
                "- "
                + format_jira_issue_markdown(
                    key=ekey,
                    url=str(epic.get("url") or "").strip(),
                    summary=str(epic.get("summary") or ekey).strip(),
                    include_transition_button=False,
                )
            )
        if epic_lines:
            lines.append("Open Epics:\n" + "\n".join(epic_lines))
    text = "\n\n".join(lines).strip()
    return text or None
