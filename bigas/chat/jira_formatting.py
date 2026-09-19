"""Shared Jira formatting rules for chat agents and tool result humanization."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

# Appended to every chat agent system prompt at runtime.
JIRA_FORMATTING_RULES = """
Jira ticket formatting (mandatory):
- Reply in the user's language.
- When you should file work on the internal Bigas board (or Jira), call create_ticket yourself (Task, Feature, or Bug only — never Epics). Use Feature for new user-facing product work. Never tell the user to create the issue themselves.
- Pass project_key (e.g. GPWW, VFA, BIG). For marketing/website/SEO/content/ads work, set marketing=true.
- To put a new ticket in a column, pass status on create_ticket (e.g. "Final Review"). To move an existing ticket, call update_ticket with issue_key and status. Do not tell the user to drag the card.
- Use lookup_ticket when you need issue details or a project's open Epics. issue_key accepts several keys or a range (BIG-15 to BIG-18). Do not ask the user for an Epic key if you can look it up.
- Use search_tickets with JQL when the user described a filter (status, type, text) without naming keys. Do not invent issue keys.
- Before opening a VFA pull request, call lookup_board_releases with project_key=VFA and use pr_base. Never use the current checkout or a released/PR-locked cut (releases[].released, releases[].pr_locked, or forbidden_pr_bases). If the ticket fix_version is released or pr_locked, ignore it and use pr_base unless the PR is a labeled hotfix. If lookup_board_releases is unavailable, fail closed with `gh release list` / `gh release view vX.Y.Z` instead of guessing from git staging-* branches.
- After lookup_ticket, search_tickets, or any tool, interpret the user's question and answer it. Tools are evidence, not the reply. Include agent and PR links when the lookup has them. Never reply with only ticket links, Open Epics, or a Move button. The Move button is a footer after the answer.
- A ticket you looked up does not mean the new work belongs under the same Epic. Set parent_epic_key only when the new Task/Bug/Feature clearly belongs under that Epic's goal. Otherwise omit parent_epic_key and create a standalone ticket — that is valid and often correct. Never invent a parent, and never use a Task, Bug, or Feature as parent.
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
    extras: list[str] = []
    agent = str(issue.get("agent_url") or "").strip()
    review = issue.get("review") if isinstance(issue.get("review"), dict) else {}
    pr_url = str(issue.get("pr_url") or review.get("pr_url") or "").strip()
    if agent:
        extras.append(f"Agent: {agent}")
    if pr_url:
        extras.append(f"PR: {pr_url}")
    extra_block = ("\n" + "\n".join(extras)) if extras else ""
    if status and not include_transition_button:
        return f"{link} — {status}{date_bit}{extra_block}"
    if status:
        return f"{link}\nStatus: {status}{date_bit}{extra_block}"
    return f"{link}{date_bit}{extra_block}"


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
        lines.append(_format_lookup_issue_line(issue, include_transition_button=False))
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


def is_jira_lookup_tool_payload(payload: Dict[str, Any]) -> bool:
    """True for lookup_ticket / search_tickets structured results (not create_ticket)."""
    if not isinstance(payload, dict) or not payload.get("ok"):
        return False
    if payload.get("jql") is not None:
        return True
    if isinstance(payload.get("issues"), list):
        return True
    if isinstance(payload.get("issue"), dict):
        return True
    if isinstance(payload.get("epics"), list):
        return True
    return False


def _lookup_issue_fact_lines(issue: Dict[str, Any]) -> List[str]:
    from bigas.tickets.review import infer_agent_url

    key = str(issue.get("key") or "").strip()
    if not key:
        return []
    review = issue.get("review") if isinstance(issue.get("review"), dict) else {}
    lines = [
        f"key: {key}",
        f"summary: {str(issue.get('summary') or issue.get('title') or key).strip()}",
    ]
    status = str(issue.get("status") or "").strip()
    if status:
        lines.append(f"status: {status}")
    for field in ("issue_type", "fix_version", "url"):
        val = str(issue.get(field) or "").strip()
        if val:
            lines.append(f"{field}: {val}")
    agent_url = infer_agent_url(issue) or str(issue.get("agent_url") or "").strip()
    if agent_url:
        lines.append(f"agent_url: {agent_url}")
    pr_url = str(issue.get("pr_url") or review.get("pr_url") or "").strip()
    if pr_url:
        lines.append(f"pr_url: {pr_url}")
    pr_title = str(issue.get("pr_title") or review.get("pr_title") or "").strip()
    if pr_title:
        lines.append(f"pr_title: {pr_title}")
    parent = issue.get("parent")
    if isinstance(parent, dict) and (parent.get("key") or "").strip():
        pkey = str(parent.get("key") or "").strip()
        psum = str(parent.get("summary") or pkey).strip()
        ptype = str(parent.get("issue_type") or "parent").strip()
        lines.append(f"parent ({ptype}): {pkey} — {psum}")
    return lines


def jira_lookup_tool_facts(payload: Dict[str, Any]) -> Optional[str]:
    """
    Plain-text ticket facts for the chat agent loop.

    Unlike humanize_jira_tool_result, this keeps agent_url, pr_url, and status
    for reasoning and does not include Move-button markdown.
    """
    if not is_jira_lookup_tool_payload(payload):
        return None
    chunks: List[str] = []
    guidance = str(payload.get("parent_guidance") or "").strip()
    if guidance:
        chunks.append(f"Note: {guidance}")

    issue_rows = [
        row
        for row in (payload.get("issues") or [])
        if isinstance(row, dict) and (row.get("key") or "").strip()
    ]
    single = payload.get("issue") if isinstance(payload.get("issue"), dict) else None
    if not issue_rows and single and (single.get("key") or "").strip():
        issue_rows = [single]

    if issue_rows:
        if len(issue_rows) == 1:
            chunks.append("\n".join(_lookup_issue_fact_lines(issue_rows[0])))
        else:
            blocks = []
            for row in issue_rows:
                block = "\n".join(_lookup_issue_fact_lines(row))
                if block:
                    blocks.append(block)
            if blocks:
                chunks.append("\n\n".join(blocks))

    missing = payload.get("missing")
    if isinstance(missing, list) and missing:
        chunks.append("Missing keys: " + ", ".join(str(k) for k in missing if k))

    jql = str(payload.get("jql") or "").strip()
    if jql:
        count = payload.get("count")
        header = f"JQL: {jql}"
        if count is not None:
            header += f" ({count} issues)"
        chunks.append(header)
        if not issue_rows:
            chunks.append("No matching issues.")

    epics = payload.get("epics")
    if isinstance(epics, list) and epics:
        epic_lines = []
        for epic in epics:
            if not isinstance(epic, dict):
                continue
            ekey = str(epic.get("key") or "").strip()
            if not ekey:
                continue
            epic_lines.append(
                f"- {ekey}: {str(epic.get('summary') or ekey).strip()} "
                f"({str(epic.get('status') or '').strip()})".strip()
            )
        if epic_lines:
            chunks.append("Open Epics:\n" + "\n".join(epic_lines))

    text = "\n\n".join(part.strip() for part in chunks if part and str(part).strip()).strip()
    return text or None
